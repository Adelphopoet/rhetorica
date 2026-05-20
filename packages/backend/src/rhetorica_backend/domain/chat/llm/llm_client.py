"""LLM client for OpenAI-compatible API calls."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.infra.logging import (
    build_llm_request_log,
    build_llm_response_log,
    get_logger,
    llm_content_logging_enabled,
    sanitize_for_logging,
)


@dataclass(frozen=True, slots=True)
class LlmCallUsage:
    """Persisted usage data for a single LLM API call."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: float
    retries: int
    phase: str = "tool_loop"


@dataclass
class LlmCompletionRequest:
    """Request for LLM chat completion."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_schema: dict[str, Any] | None = None
    tool_choice: Literal["auto", "required"] | None = None


@dataclass
class LlmCompletionResponse:
    """Response from LLM chat completion."""

    message: dict[str, Any]
    raw_body: dict[str, Any]


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

    def __init__(self, connection: LlmConnectionConfig):
        self._connection = connection
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
    def _build_headers(connection: LlmConnectionConfig) -> dict[str, str]:
        """Build HTTP headers for LLM API requests."""
        headers = {"Content-Type": "application/json", **connection.headers}
        if connection.api_key:
            headers["Authorization"] = f"Bearer {connection.api_key}"
        return headers

    @staticmethod
    def _build_payload(
        connection: LlmConnectionConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        tool_choice: Literal["auto", "required"] | None = None,
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
        if connection.max_tokens is not None:
            payload["max_tokens"] = connection.max_tokens
        if response_schema and LlmClient._structured_output_enabled(connection):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "chat_final_response_v1",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        return payload

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

    def build_prompt_budget_service(self) -> LlmPromptBudgetService:
        """Build request-budget service from connector settings."""
        return LlmPromptBudgetService(
            model_name=self._connection.model,
            context_window_tokens=self._context_window_tokens(self._connection),
            response_reserve_tokens=self._connection.max_tokens
            or LlmPromptBudgetService.DEFAULT_RESPONSE_RESERVE_TOKENS,
        )

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
        tool_choice: Literal["auto", "required"] | None = None,
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
        headers = self._build_headers(self._connection)
        payload = self._build_payload(
            self._connection,
            messages,
            tools,
            response_schema=response_schema,
            tool_choice=tool_choice,
        )
        include_content = self._include_llm_content()
        attempt_count = 0
        last_duration_ms = 0.0

        def _request() -> dict[str, Any]:
            nonlocal attempt_count, last_duration_ms
            attempt_count += 1
            with httpx.Client(timeout=45.0, verify=self._connection.verify_tls) as client:
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
                        response_text=sanitize_for_logging(response.text),
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

        message = self._extract_message(body)

        # Extract usage from response body and record for persistence
        usage = body.get("usage", {})
        if isinstance(usage, dict):
            retries = max(0, attempt_count - 1)
            self._call_usages.append(
                LlmCallUsage(
                    model=self._connection.model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    duration_ms=last_duration_ms,
                    retries=retries,
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
        headers = self._build_headers(self._connection)
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
        if self._connection.max_tokens is not None:
            payload["max_output_tokens"] = self._connection.max_tokens

        include_content = self._include_llm_content()
        attempt_count = 0
        last_duration_ms = 0.0

        def _request() -> dict[str, Any]:
            nonlocal attempt_count, last_duration_ms
            attempt_count += 1
            with httpx.Client(timeout=45.0, verify=self._connection.verify_tls) as client:
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
                        response_text=sanitize_for_logging(response.text),
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
        usage = body.get("usage", {})
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
            self._call_usages.append(
                LlmCallUsage(
                    model=self._connection.model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=total_tokens,
                    duration_ms=last_duration_ms,
                    retries=max(0, attempt_count - 1),
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
        tool_choice: Literal["auto", "required"] | None = None,
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

    def _retry_attempts(self) -> int:
        raw = self._connection.extra_settings.get("llm_retry_attempts")
        if isinstance(raw, int | float):
            value = int(raw)
            if 1 <= value <= 5:
                return value
        return 3

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
