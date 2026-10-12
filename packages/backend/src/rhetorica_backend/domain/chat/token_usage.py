"""Conversation-scoped accumulation of provider-reported LLM token usage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationTokenUsage:
    """Non-negative input/output token totals for one conversation or turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_usage_reported_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_llm_usages(cls, usages: Iterable[Any] | None) -> ConversationTokenUsage:
        """Sum only the supplied call usages; persisted diagnostics are never read."""
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        cache_write_input_tokens = 0
        cache_usage_reported_calls = 0
        for usage in usages or ():
            input_tokens += cls._token_count(
                usage,
                primary_name="input_tokens",
                compatibility_name="prompt_tokens",
            )
            output_tokens += cls._token_count(
                usage,
                primary_name="output_tokens",
                compatibility_name="completion_tokens",
            )
            cached_input_tokens += cls._token_count(
                usage,
                primary_name="cached_input_tokens",
                compatibility_name="cached_tokens",
            )
            cache_write_input_tokens += cls._token_count(
                usage,
                primary_name="cache_write_input_tokens",
                compatibility_name="cache_write_tokens",
            )
            if cls._reported(usage):
                cache_usage_reported_calls += 1
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            cache_usage_reported_calls=cache_usage_reported_calls,
        )

    @staticmethod
    def _reported(usage: Any) -> bool:
        if isinstance(usage, Mapping):
            return usage.get("cache_usage_reported") is True
        return getattr(usage, "cache_usage_reported", False) is True

    @staticmethod
    def _token_count(
        usage: Any,
        *,
        primary_name: str,
        compatibility_name: str,
    ) -> int:
        if isinstance(usage, Mapping):
            value = usage.get(primary_name)
            if value is None:
                value = usage.get(compatibility_name)
        else:
            value = getattr(usage, primary_name, None)
            if value is None:
                value = getattr(usage, compatibility_name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value
