"""LLM client for OpenAI-compatible API calls."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import urljoin

import httpx
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.domain.chat.settings import get_chat_settings
from rhetorica_backend.infra.logging import (
    build_llm_request_log,
    build_llm_response_log,
    get_logger,
    llm_content_logging_enabled,
    llm_payload_contains_file_tools,
    sanitize_for_logging,
)


class LlmFunctionToolChoiceTarget(TypedDict):
    name: str


class LlmFunctionToolChoice(TypedDict):
    type: Literal["function"]
    function: LlmFunctionToolChoiceTarget


LlmToolChoice = Literal["auto", "none"] | LlmFunctionToolChoice


@dataclass(slots=True)
class LlmCallDiagnostics:
    """Content-free observations of one SDK model call, timestamps in UTC."""

    run_id: str
    call_id: str
    call_index: int
    streamed: bool
    started_at: str
    status: str = "running"
    ended_at: str | None = None
    first_chunk_at: str | None = None
    last_chunk_at: str | None = None
    last_chunk_type: str | None = None
    text_chars: int = 0
    reasoning_chars: int = 0
    tool_arguments_chars: int = 0
    content_event_count: int = 0
    terminal_event: str | None = None
    response_id: str | None = None
    provider_request_id: str | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class LlmCallUsage:
    """Persisted usage data for a single LLM API call."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: float
    retries: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_usage_reported: bool = False
    phase: str = "tool_loop"
    usage_reported: bool = True
    diagnostics: LlmCallDiagnostics | None = None


def _nonnegative_usage_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return int(value)


def _normalized_cache_usage(usage: dict[str, Any]) -> tuple[int, int, bool]:
    """Normalize OpenAI-compatible and Anthropic prompt-cache usage fields."""
    details: dict[str, Any] = {}
    for key in ("prompt_tokens_details", "input_tokens_details"):
        candidate = usage.get(key)
        if isinstance(candidate, dict):
            details = candidate
            break

    read_keys = ("cached_tokens", "cache_read_input_tokens")
    write_keys = (
        "cache_write_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
    )
    reported = any(key in details for key in read_keys + write_keys) or any(
        key in usage for key in read_keys + write_keys
    )

    def first_count(keys: tuple[str, ...]) -> int:
        for source in (details, usage):
            for key in keys:
                if key in source:
                    return _nonnegative_usage_int(source.get(key))
        return 0

    return first_count(read_keys), first_count(write_keys), reported


@dataclass
class LlmCompletionRequest:
    """Request for LLM chat completion."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_schema: dict[str, Any] | None = None
    tool_choice: LlmToolChoice | None = None


@dataclass
class LlmCompletionResponse:
    """Response from LLM chat completion."""

    message: dict[str, Any]
    raw_body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LlmTurnRequest:
    """Normalized single-turn request used by orchestration runtime."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None
    tool_choice: LlmToolChoice | None = None


@dataclass(frozen=True, slots=True)
class LlmTurnResult:
    """Normalized single-turn result independent of provider transport details."""

    response: LlmCompletionResponse
    output_text: str
    tool_calls: list[dict[str, Any]]
    tools_disabled: bool = False
    structured_disabled: bool = False


class LlmTurnClient(Protocol):
    """Port consumed by the tool loop for one model turn."""

    def create_turn(self, request: LlmTurnRequest) -> LlmTurnResult: ...


@dataclass(frozen=True, slots=True)
class LlmFunctionCall:
    """Native Responses API function call emitted by the model."""

    call_id: str
    name: str
    arguments_json: str


@dataclass
class LlmResponsesResult:
    """Normalized OpenAI Responses API result."""

    response_id: str | None
    output_text: str
    function_calls: list[LlmFunctionCall]
    raw_body: dict[str, Any]


class LlmClient:
    """Client for OpenAI-compatible LLM APIs."""

    def __init__(
        self,
        connection: LlmConnectionConfig,
        *,
        request_timeout: float | None = None,
    ):
        self._connection = connection
        self._request_timeout = (
            request_timeout
            if request_timeout is not None
            else get_chat_settings().llm_request_timeout
        )
        self._logger = get_logger(__name__, llm_model=connection.model)
        self._call_usages: list[LlmCallUsage] = []

    def collect_usages(self, phase: str | None = None) -> list[LlmCallUsage]:
        """Return and clear accumulated LLM call usages."""
        usages = list(self._call_usages)
        self._call_usages.clear()
        if phase is not None:
            usages = [replace(usage, phase=phase) for usage in usages]
        return usages

    @property
    def connection(self) -> LlmConnectionConfig:
        """Expose connector config for transport-level runtime selection."""
        return self._connection

    @staticmethod
    def _build_endpoint(base_url: str, endpoint_path: str) -> str:
        """Build full endpoint URL from base URL and path."""
        if endpoint_path.startswith("http://") or endpoint_path.startswith("https://"):
            return endpoint_path
        return urljoin(base_url.rstrip("/") + "/", endpoint_path.lstrip("/"))

    @staticmethod
    def _build_responses_endpoint(connection: LlmConnectionConfig) -> str:
        """Build the strict OpenAI Responses endpoint.

        `endpoint_path` is intentionally not used for chat completions anymore. A
        connector may override this with `responses_endpoint_path`, but the
        runtime defaults to `/responses`.
        """
        raw_path = connection.extra_settings.get("responses_endpoint_path")
        endpoint_path = raw_path if isinstance(raw_path, str) and raw_path else "/responses"
        return LlmClient._build_endpoint(
            base_url=connection.base_url,
            endpoint_path=endpoint_path,
        )

    @staticmethod
    def _build_headers(
        connection: LlmConnectionConfig,
        *,
        api_key_index: int | None = None,
    ) -> dict[str, str]:
        """Build HTTP headers for LLM API requests."""
        headers = {"Content-Type": "application/json", **connection.headers}
        api_key = LlmClient._select_api_key(connection, api_key_index=api_key_index)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _select_api_key(
        connection: LlmConnectionConfig,
        *,
        api_key_index: int | None = None,
    ) -> str | None:
        if connection.api_keys:
            if api_key_index is not None:
                return connection.api_keys[api_key_index % len(connection.api_keys)]
            return secrets.choice(connection.api_keys)
        return connection.api_key

    @staticmethod
    def _build_payload(
        connection: LlmConnectionConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        tool_choice: LlmToolChoice | None = None,
    ) -> dict[str, Any]:
        """Build request payload for LLM API."""
        payload: dict[str, Any] = {
            "model": connection.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if connection.temperature is not None:
            payload["temperature"] = connection.temperature
        if connection.top_p is not None:
            payload["top_p"] = connection.top_p
        if connection.reasoning_effort is not None:
            payload["reasoning_effort"] = connection.reasoning_effort
        if connection.max_tokens is not None:
            payload[connection.max_tokens_parameter] = connection.max_tokens
        if response_schema and not tools and LlmClient._structured_output_enabled(connection):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "chat_final_response_v1",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        if LlmClient._reasoning_split_enabled(connection):
            payload["reasoning_split"] = True
        chat_template_enable_thinking = connection.extra_settings.get(
            "chat_template_enable_thinking"
        )
        if isinstance(chat_template_enable_thinking, bool):
            payload["chat_template_kwargs"] = {
                "enable_thinking": chat_template_enable_thinking,
            }
        return payload

    @staticmethod
    def _reasoning_split_enabled(connection: LlmConnectionConfig) -> bool:
        """Enable ExampleReasoningModel-compatible reasoning split payload extension."""
        if connection.model == "ExampleReasoningModel":
            return True
        extra = connection.extra_settings
        for key in ("example_reasoning_compatible", "reasoning_split"):
            raw = extra.get(key)
            if isinstance(raw, bool) and raw:
                return True
        return False

    @staticmethod
    def _structured_output_enabled(connection: LlmConnectionConfig) -> bool:
        """Read structured mode capability flags from connector extra settings."""
        raw = connection.extra_settings.get("structured_output_enabled")
        if isinstance(raw, bool):
            return raw
        raw_legacy = connection.extra_settings.get("force_text_output")
        if isinstance(raw_legacy, bool):
            return not raw_legacy
        return True

    @staticmethod
    def _explicit_final_answer_enabled(connection: LlmConnectionConfig) -> bool:
        """Require final answers to use the SDK-native finalization tool."""
        raw = connection.extra_settings.get("require_explicit_final_answer")
        return raw if isinstance(raw, bool) else False

    def build_prompt_budget_service(self) -> LlmPromptBudgetService:
        """Build request-budget service from connector settings."""
        return LlmPromptBudgetService(
            model_name=self._connection.model,
            context_window_tokens=self._context_window_tokens(self._connection),
            response_reserve_tokens=self._connection.max_tokens
            or LlmPromptBudgetService.DEFAULT_RESPONSE_RESERVE_TOKENS,
        )

    def summarize_context_for_compaction(
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Summarize a completed prompt prefix without exposing any tools."""
        response = self.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create a concise continuation checkpoint for the supplied "
                        "conversation prefix. Preserve the user's current goal, explicit "
                        "constraints, decisions, concrete facts, exact identifiers, exact "
                        "SQL when present, errors, unresolved questions, and the next step. "
                        "Do not call tools. Do not invent facts. Do not reproduce bulky tool "
                        "outputs; the backend supplies an authoritative executed-call ledger "
                        "and artifact handles separately. Return only the checkpoint text."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(messages, ensure_ascii=False, sort_keys=True),
                },
            ],
            tools=None,
            response_schema=None,
            tool_choice=None,
        )
        content = response.message.get("content")
        return content if isinstance(content, str) else ""

    @staticmethod
    def _context_window_tokens(connection: LlmConnectionConfig) -> int | None:
        raw = connection.extra_settings.get("context_window_tokens")
        if raw is None:
            raw = connection.extra_settings.get("context_window")
        if isinstance(raw, int | float):
            value = int(raw)
            return value if value > 0 else None
        return None

    @staticmethod
    def _extract_message(body: dict[str, Any]) -> dict[str, Any]:
        """Extract message from LLM API response."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return {}
        message = choices[0].get("message")
        return message if isinstance(message, dict) else {}

    @staticmethod
    def _is_tools_not_supported_error(exc: httpx.HTTPStatusError) -> bool:
        """Check if HTTP error indicates tools/functions are not supported."""
        status_code = exc.response.status_code
        if status_code not in {400, 404, 422}:
            return False
        body = exc.response.text.lower()
        return "tool" in body or "function_call" in body or "tool_choice" in body

    @staticmethod
    def _is_structured_output_not_supported_error(
        exc: httpx.HTTPStatusError,
    ) -> bool:
        """Check if HTTP error indicates strict schema mode is unsupported."""
        status_code = exc.response.status_code
        if status_code not in {400, 404, 422}:
            return False
        body = exc.response.text.lower()
        return "response_format" in body or "json_schema" in body or "structured output" in body

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        tool_choice: LlmToolChoice | None = None,
    ) -> LlmCompletionResponse:
        """
        Send chat completion request to LLM API.

        Args:
            messages: List of message dicts with role and content.
            tools: Optional list of tool definitions.

        Returns:
            LlmCompletionResponse with extracted message and raw body.

        Raises:
            httpx.HTTPStatusError: If API request fails.
        """
        endpoint = self._build_endpoint(
            base_url=self._connection.base_url,
            endpoint_path=self._connection.endpoint_path,
        )
        payload = self._build_payload(
            self._connection,
            messages,
            tools,
            response_schema=response_schema,
            tool_choice=tool_choice,
        )
        contains_file_tools = llm_payload_contains_file_tools(payload)
        include_content = self._include_llm_content() and not contains_file_tools
        attempt_count = 0
        last_duration_ms = 0.0
        api_key_offset = self._api_key_retry_offset()

        def _request() -> dict[str, Any]:
            nonlocal attempt_count, last_duration_ms
            attempt_count += 1
            headers = self._build_headers(
                self._connection,
                api_key_index=api_key_offset + attempt_count - 1,
            )
            with httpx.Client(
                timeout=self._request_timeout,
                verify=self._connection.verify_tls,
            ) as client:
                started_at = perf_counter()
                self._logger.info(
                    "llm.request.started",
                    **build_llm_request_log(
                        endpoint=endpoint,
                        headers=headers,
                        payload=payload,
                        include_content=include_content,
                    ),
                    retry_attempts=self._retry_attempts(),
                )
                response = client.post(endpoint, headers=headers, json=payload)
                last_duration_ms = round((perf_counter() - started_at) * 1000, 2)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    self._logger.warning(
                        "llm.request.http_error",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        duration_ms=last_duration_ms,
                        response_text=(
                            "[REDACTED:file_tool_payload]"
                            if contains_file_tools
                            else sanitize_for_logging(response.text)
                        ),
                    )
                    raise
                body = response.json()
                self._logger.info(
                    "llm.request.completed",
                    **build_llm_response_log(
                        body=body,
                        include_content=include_content,
                    ),
                    endpoint=endpoint,
                    status_code=response.status_code,
                    duration_ms=last_duration_ms,
                )
                return body

        body = retry_idempotent(
            _request,
            max_attempts=self._retry_attempts(),
        )
        self._commit_successful_retry(
            api_key_offset=api_key_offset,
            attempt_count=attempt_count,
        )

        message = self._extract_message(body)

        # Extract usage from response body and record for persistence
        usage = body.get("usage", {})
        if isinstance(usage, dict):
            retries = max(0, attempt_count - 1)
            cached_tokens, cache_write_tokens, cache_reported = _normalized_cache_usage(usage)
            self._call_usages.append(
                LlmCallUsage(
                    model=self._connection.model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    duration_ms=last_duration_ms,
                    retries=retries,
                    cached_input_tokens=cached_tokens,
                    cache_write_input_tokens=cache_write_tokens,
                    cache_usage_reported=cache_reported,
                )
            )

        return LlmCompletionResponse(message=message, raw_body=body)

    @staticmethod
    def _responses_instructions_and_input(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if role == "system":
                instructions.append(content)
                continue
            if role in {"user", "assistant", "developer"}:
                input_items.append({"role": role, "content": content})
        return ("\n\n".join(instructions) if instructions else None), input_items

    @staticmethod
    def _responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Convert legacy chat-completions function specs to Responses specs."""
        converted: list[dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            if not isinstance(name, str) or not isinstance(parameters, dict):
                continue
            converted.append({
                "type": "function",
                "name": name,
                "description": function.get("description") or "",
                "parameters": parameters,
                "strict": True,
            })
        return converted

    @staticmethod
    def _response_text_format(response_schema: dict[str, Any] | None) -> dict[str, Any] | None:
        if response_schema is None:
            return None
        return {
            "format": {
                "type": "json_schema",
                "name": "chat_final_response_v1",
                "strict": True,
                "schema": response_schema,
            }
        }

    @staticmethod
    def _extract_responses_output_text(body: dict[str, Any]) -> str:
        output_text = body.get("output_text")
        if isinstance(output_text, str):
            return output_text

        parts: list[str] = []
        output = body.get("output")
        if not isinstance(output, list):
            return ""
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def _extract_responses_function_calls(body: dict[str, Any]) -> list[LlmFunctionCall]:
        calls: list[LlmFunctionCall] = []
        output = body.get("output")
        if not isinstance(output, list):
            return calls
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = item.get("name")
            call_id = item.get("call_id")
            arguments = item.get("arguments")
            if not isinstance(name, str) or not isinstance(call_id, str):
                continue
            calls.append(
                LlmFunctionCall(
                    call_id=call_id,
                    name=name,
                    arguments_json=arguments if isinstance(arguments, str) else "{}",
                )
            )
        return calls

    def responses_completion(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        input_items: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        previous_response_id: str | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
    ) -> LlmResponsesResult:
        """Call strict OpenAI Responses API with native function tools."""
        endpoint = self._build_responses_endpoint(self._connection)
        instructions: str | None = None
        resolved_input: list[dict[str, Any]]
        if input_items is not None:
            resolved_input = input_items
        else:
            instructions, resolved_input = self._responses_instructions_and_input(messages or [])

        payload: dict[str, Any] = {
            "model": self._connection.model,
            "input": resolved_input,
        }
        if instructions:
            payload["instructions"] = instructions
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        converted_tools = self._responses_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        text_format = self._response_text_format(response_schema)
        if text_format is not None:
            payload["text"] = text_format
        if self._connection.temperature is not None:
            payload["temperature"] = self._connection.temperature
        if self._connection.top_p is not None:
            payload["top_p"] = self._connection.top_p
        if self._connection.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._connection.reasoning_effort}
        if self._connection.max_tokens is not None:
            payload["max_output_tokens"] = self._connection.max_tokens

        contains_file_tools = llm_payload_contains_file_tools(payload)
        include_content = self._include_llm_content() and not contains_file_tools
        attempt_count = 0
        last_duration_ms = 0.0
        api_key_offset = self._api_key_retry_offset()

        def _request() -> dict[str, Any]:
            nonlocal attempt_count, last_duration_ms
            attempt_count += 1
            headers = self._build_headers(
                self._connection,
                api_key_index=api_key_offset + attempt_count - 1,
            )
            with httpx.Client(
                timeout=self._request_timeout,
                verify=self._connection.verify_tls,
            ) as client:
                started_at = perf_counter()
                self._logger.info(
                    "llm.responses.request.started",
                    **build_llm_request_log(
                        endpoint=endpoint,
                        headers=headers,
                        payload=payload,
                        include_content=include_content,
                    ),
                    retry_attempts=self._retry_attempts(),
                )
                response = client.post(endpoint, headers=headers, json=payload)
                last_duration_ms = round((perf_counter() - started_at) * 1000, 2)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    self._logger.warning(
                        "llm.responses.request.http_error",
                        endpoint=endpoint,
                        status_code=response.status_code,
                        duration_ms=last_duration_ms,
                        response_text=(
                            "[REDACTED:file_tool_payload]"
                            if contains_file_tools
                            else sanitize_for_logging(response.text)
                        ),
                    )
                    raise
                body = response.json()
                self._logger.info(
                    "llm.responses.request.completed",
                    **build_llm_response_log(
                        body=body,
                        include_content=include_content,
                    ),
                    endpoint=endpoint,
                    status_code=response.status_code,
                    duration_ms=last_duration_ms,
                )
                return body

        body = retry_idempotent(_request, max_attempts=self._retry_attempts())
        self._commit_successful_retry(
            api_key_offset=api_key_offset,
            attempt_count=attempt_count,
        )
        usage = body.get("usage", {})
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
            cached_tokens, cache_write_tokens, cache_reported = _normalized_cache_usage(usage)
            self._call_usages.append(
                LlmCallUsage(
                    model=self._connection.model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=total_tokens,
                    duration_ms=last_duration_ms,
                    retries=max(0, attempt_count - 1),
                    cached_input_tokens=cached_tokens,
                    cache_write_input_tokens=cache_write_tokens,
                    cache_usage_reported=cache_reported,
                )
            )
        response_id = body.get("id")
        return LlmResponsesResult(
            response_id=response_id if isinstance(response_id, str) else None,
            output_text=self._extract_responses_output_text(body),
            function_calls=self._extract_responses_function_calls(body),
            raw_body=body,
        )

    def chat_completion_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        tool_choice: LlmToolChoice | None = None,
    ) -> tuple[LlmCompletionResponse, bool, bool]:
        """
        Send chat completion with fallback to non-tools mode if not supported.

        Args:
            messages: List of message dicts with role and content.
            tools: Optional list of tool definitions.

        Returns:
            Tuple of (LlmCompletionResponse, tools_disabled_flag, structured_disabled_flag).
            tools_disabled_flag is True if tools were disabled due to lack of support.
            structured_disabled_flag is True if response schema mode was disabled.

        Raises:
            httpx.HTTPStatusError: If API request fails (non-tools related).
        """
        tools_disabled = False
        structured_disabled = False
        try:
            response = self.chat_completion(
                messages,
                tools,
                response_schema=response_schema,
                tool_choice=tool_choice,
            )
        except httpx.HTTPStatusError as exc:
            if tools and self._is_tools_not_supported_error(exc):
                self._logger.warning(
                    "llm.request.tools_unsupported",
                    status_code=exc.response.status_code,
                )
                tools_disabled = True
                try:
                    response = self.chat_completion(
                        messages,
                        tools=[],
                        response_schema=response_schema,
                        tool_choice=tool_choice,
                    )
                except httpx.HTTPStatusError as inner_exc:
                    if response_schema and self._is_structured_output_not_supported_error(
                        inner_exc
                    ):
                        self._logger.warning(
                            "llm.request.structured_output_unsupported",
                            status_code=inner_exc.response.status_code,
                        )
                        response = self.chat_completion(messages, tools=[])
                        structured_disabled = True
                        return response, tools_disabled, structured_disabled
                    raise
                else:
                    return response, tools_disabled, structured_disabled
            if response_schema and self._is_structured_output_not_supported_error(exc):
                self._logger.warning(
                    "llm.request.structured_output_unsupported",
                    status_code=exc.response.status_code,
                )
                response = self.chat_completion(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                structured_disabled = True
                return response, tools_disabled, structured_disabled
            raise
        else:
            return response, tools_disabled, structured_disabled

    def create_turn(self, request: LlmTurnRequest) -> LlmTurnResult:
        """Create one normalized model turn for orchestration.

        This wraps the existing OpenAI-compatible transport behind a small port.
        The runtime can move to native Responses state handling without changing
        terminal policy or tool execution contracts.
        """
        response, tools_disabled, structured_disabled = self.chat_completion_with_fallback(
            messages=request.messages,
            tools=request.tools,
            response_schema=request.response_schema,
            tool_choice=request.tool_choice,
        )
        content = response.message.get("content")
        tool_calls = response.message.get("tool_calls")
        return LlmTurnResult(
            response=response,
            output_text=content if isinstance(content, str) else "",
            tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            tools_disabled=tools_disabled,
            structured_disabled=structured_disabled,
        )

    def _retry_attempts(self) -> int:
        raw = self._connection.extra_settings.get("llm_retry_attempts")
        if isinstance(raw, int | float):
            value = int(raw)
            if 1 <= value <= 5:
                return value
        return 3

    def _api_key_retry_offset(self) -> int:
        if not self._connection.api_keys:
            return 0
        if self._connection.selected_api_key_index is not None:
            return self._connection.selected_api_key_index
        return secrets.randbelow(len(self._connection.api_keys))

    def _commit_successful_retry(self, *, api_key_offset: int, attempt_count: int) -> None:
        """Persist a credential switch only after the retry chain succeeds."""
        if attempt_count <= 1 or not self._connection.api_keys:
            return
        selected_index = (api_key_offset + attempt_count - 1) % len(self._connection.api_keys)
        expected_index = self._connection.selected_api_key_index
        if expected_index is None:
            return
        self._connection.commit_credential_retry(
            selected_api_key_index=selected_index,
            expected_index=expected_index,
        )

    def _include_llm_content(self) -> bool:
        configured = self._connection.extra_settings.get("log_llm_content")
        if not isinstance(configured, bool):
            raw_env = os.getenv("LOG_LLM_CONTENT")
            if raw_env is not None:
                configured = raw_env.strip().lower() in {"1", "true", "yes", "on"}
            else:
                configured = None
        return llm_content_logging_enabled(
            environment=(
                os.getenv("APP_ENV")
                or os.getenv("API_APP_ENV")
                or os.getenv("WORKER_APP_ENV")
                or "dev"
            ),
            configured_value=configured,
        )
