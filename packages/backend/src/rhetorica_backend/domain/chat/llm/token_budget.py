"""Token estimation and prompt budget enforcement for LLM requests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenEstimate:
    """Estimated token count with estimator diagnostics."""

    tokens: int
    used_fallback: bool
    unknown_model: bool


@dataclass(frozen=True)
class PromptBudgetResult:
    """Budget application result for one LLM request."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    estimated_tokens: int
    budget_tokens: int
    warnings: list[str]


class LlmTokenEstimator:
    """Token estimator with tiktoken adapter and conservative fallback."""

    _FALLBACK_CHARS_PER_TOKEN = 2
    _TOKENS_PER_MESSAGE = 4
    _TOKENS_PER_NAME = 1
    _REQUEST_OVERHEAD_TOKENS = 3
    _ENABLE_TIKTOKEN_ENV = "RHETORICA_ENABLE_TIKTOKEN_ESTIMATOR"
    _KNOWN_MODEL_PREFIXES = ("gpt-4o", "o1", "o3", "o4", "gpt-5", "gpt-4", "gpt-3.5")

    def __init__(self, model_name: str | None):
        self._encoding = None
        self.used_fallback = False
        self.unknown_model = not self._is_known_model(model_name)
        if not self._tiktoken_enabled():
            self.used_fallback = True
            return
        try:
            import tiktoken  # type: ignore[import-not-found]

            if model_name:
                try:
                    self._encoding = tiktoken.encoding_for_model(model_name)
                except KeyError:
                    self.unknown_model = True
                    self._encoding = tiktoken.get_encoding("cl100k_base")
            else:
                self.unknown_model = True
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.used_fallback = True
            self._encoding = None

    @classmethod
    def _tiktoken_enabled(cls) -> bool:
        return os.getenv(cls._ENABLE_TIKTOKEN_ENV, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @classmethod
    def _is_known_model(cls, model_name: str | None) -> bool:
        if not isinstance(model_name, str) or not model_name.strip():
            return False
        return model_name.strip().lower().startswith(cls._KNOWN_MODEL_PREFIXES)

    def count_text(self, text: str) -> int:
        """Estimate tokens in plain text."""
        if not text:
            return 0
        if self._encoding is not None:
            return len(self._encoding.encode(text))
        return max(1, (len(text) + self._FALLBACK_CHARS_PER_TOKEN - 1) // self._FALLBACK_CHARS_PER_TOKEN)

    def count_json(self, value: Any) -> int:
        """Estimate tokens in a compact JSON payload."""
        return self.count_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def count_message(self, message: dict[str, Any]) -> int:
        """Estimate one OpenAI-compatible chat message including framing overhead."""
        total = self._TOKENS_PER_MESSAGE
        for key, value in message.items():
            if value is None:
                continue
            if isinstance(value, str):
                total += self.count_text(value)
            else:
                total += self.count_json(value)
            if key == "name":
                total += self._TOKENS_PER_NAME
        return total

    def count_request(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
        response_reserve_tokens: int = 0,
    ) -> TokenEstimate:
        """Estimate the full prompt request, not only message content."""
        total = self._REQUEST_OVERHEAD_TOKENS + max(0, response_reserve_tokens)
        total += sum(self.count_message(message) for message in messages)
        if tools:
            total += self.count_json(tools)
        if response_schema:
            total += self.count_json(response_schema)
        return TokenEstimate(
            tokens=total,
            used_fallback=self.used_fallback,
            unknown_model=self.unknown_model,
        )


class LlmPromptBudgetService:
    """Applies prompt budget while preserving the current user request and system contract."""

    DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS = 16_384
    DEFAULT_RESPONSE_RESERVE_TOKENS = 4_000
    MAX_RESPONSE_RESERVE_RATIO = 4
    MIN_PROMPT_BUDGET_TOKENS = 512

    def __init__(
        self,
        *,
        model_name: str | None,
        context_window_tokens: int | None = None,
        response_reserve_tokens: int = DEFAULT_RESPONSE_RESERVE_TOKENS,
    ) -> None:
        self._model_name = model_name
        self._context_window_tokens = context_window_tokens
        self._response_reserve_tokens = response_reserve_tokens
        self._estimator = LlmTokenEstimator(model_name=model_name)

    @property
    def estimator(self) -> LlmTokenEstimator:
        """Expose the shared estimator for callers that need compatible counts."""
        return self._estimator

    def prompt_budget_tokens(self) -> int:
        """Return available input budget after output reserve."""
        window = self.context_window_tokens()
        return max(
            self.MIN_PROMPT_BUDGET_TOKENS,
            window - self._effective_response_reserve_tokens(window),
        )

    def context_window_tokens(self) -> int:
        """Resolve model context window with conservative unknown-model default."""
        if isinstance(self._context_window_tokens, int) and self._context_window_tokens > 0:
            return self._context_window_tokens
        return self.model_context_window(self._model_name)

    @classmethod
    def model_context_window(cls, model_name: str | None) -> int:
        """Known model windows; unknown OpenAI-compatible endpoints stay conservative."""
        if not isinstance(model_name, str) or not model_name.strip():
            return cls.DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS
        normalized = model_name.strip().lower()
        if normalized.startswith(("gpt-4o", "o1", "o3", "o4", "gpt-5")):
            return 128_000
        if normalized.startswith("gpt-4"):
            return 128_000
        if normalized.startswith("gpt-3.5"):
            return 16_384
        return cls.DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS

    def diagnostics_warnings(self) -> list[str]:
        """Return static estimator diagnostics for this request scope."""
        warnings: list[str] = []
        if self._estimator.used_fallback:
            warnings.append("context_token_estimator_fallback")
        if self._estimator.unknown_model and self._context_window_tokens is None:
            warnings.append("context_window_unconfirmed")
        if self._response_reserve_tokens > self._effective_response_reserve_tokens(
            self.context_window_tokens()
        ):
            warnings.append("response_reserve_capped")
        return warnings

    def _effective_response_reserve_tokens(self, window: int) -> int:
        """Reserve output space without starving the input prompt."""
        requested = max(0, self._response_reserve_tokens)
        max_reserve = max(
            self.MIN_PROMPT_BUDGET_TOKENS,
            window // self.MAX_RESPONSE_RESERVE_RATIO,
        )
        return min(requested, max_reserve)

    def fit_request_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> PromptBudgetResult:
        """Trim low-priority historical messages until the full request fits."""
        budget_tokens = self.prompt_budget_tokens()
        warnings = self.diagnostics_warnings()
        fitted_messages = list(messages)
        fitted_tools = tools
        estimate = self._estimate(
            messages=fitted_messages,
            tools=fitted_tools,
            response_schema=response_schema,
        )
        if estimate.tokens > budget_tokens and fitted_tools:
            fitted_tools = self._compact_tool_schemas(fitted_tools)
            warnings.append("tool_schemas_compacted")
            estimate = self._estimate(
                messages=fitted_messages,
                tools=fitted_tools,
                response_schema=response_schema,
            )
        while estimate.tokens > budget_tokens:
            removable_index = self._oldest_removable_message_index(fitted_messages)
            if removable_index is None:
                warnings.append("context_budget_exceeded")
                break
            del fitted_messages[removable_index]
            if "context_truncated" not in warnings:
                warnings.append("context_truncated")
            estimate = self._estimate(
                messages=fitted_messages,
                tools=fitted_tools,
                response_schema=response_schema,
            )

        return PromptBudgetResult(
            messages=fitted_messages,
            tools=fitted_tools,
            estimated_tokens=estimate.tokens,
            budget_tokens=budget_tokens,
            warnings=warnings,
        )

    def estimate_request(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> TokenEstimate:
        """Estimate a complete request with the same accounting used by hard fitting."""
        return self._estimate(
            messages=messages,
            tools=tools,
            response_schema=response_schema,
        )

    def _estimate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        response_schema: dict[str, Any] | None,
    ) -> TokenEstimate:
        return self._estimator.count_request(
            messages=messages,
            tools=tools,
            response_schema=response_schema,
            response_reserve_tokens=self._effective_response_reserve_tokens(
                self.context_window_tokens()
            ),
        )

    @staticmethod
    def _oldest_removable_message_index(messages: list[dict[str, Any]]) -> int | None:
        """Find an old history message; never drop system, current user, or tool protocol tail."""
        last_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"),
            None,
        )
        protected = {0}
        if last_user_index is not None:
            protected.add(last_user_index)
        for index, message in enumerate(messages):
            if index in protected:
                continue
            if message.get("role") == "system":
                continue
            if message.get("role") == "tool":
                continue
            if isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
                continue
            return index
        return None

    @classmethod
    def _compact_tool_schemas(cls, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop verbose descriptions while preserving callable tool contracts."""
        compacted: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                compacted.append(tool)
                continue
            tool_copy = dict(tool)
            function = tool_copy.get("function")
            if isinstance(function, dict):
                function_copy = dict(function)
                function_copy.pop("description", None)
                parameters = function_copy.get("parameters")
                if isinstance(parameters, dict):
                    function_copy["parameters"] = cls._compact_json_schema(parameters)
                tool_copy["function"] = function_copy
            compacted.append(tool_copy)
        return compacted

    @classmethod
    def _compact_json_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        compacted: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "description":
                continue
            if isinstance(value, dict):
                compacted[key] = cls._compact_json_schema(value)
            elif isinstance(value, list):
                compacted[key] = [
                    cls._compact_json_schema(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                compacted[key] = value
        return compacted
