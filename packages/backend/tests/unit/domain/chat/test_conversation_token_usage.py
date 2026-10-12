from __future__ import annotations

from rhetorica_backend.domain.chat.llm.llm_client import LlmCallUsage
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage


def test_sums_chat_completions_and_responses_usage_fields() -> None:
    usage = ConversationTokenUsage.from_llm_usages([
        LlmCallUsage(
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
            duration_ms=10,
            retries=0,
            cached_input_tokens=80,
            cache_write_input_tokens=10,
            cache_usage_reported=True,
        ),
        {
            "input_tokens": 40,
            "output_tokens": 10,
            "total_tokens": 50,
            "cached_input_tokens": 20,
            "cache_usage_reported": True,
        },
    ])

    assert usage.input_tokens == 140
    assert usage.output_tokens == 35
    assert usage.total_tokens == 175
    assert usage.cached_input_tokens == 100
    assert usage.cache_write_input_tokens == 10
    assert usage.cache_usage_reported_calls == 2


def test_compacted_followup_adds_only_provider_reported_usage_for_each_call() -> None:
    usage = ConversationTokenUsage.from_llm_usages([
        {"prompt_tokens": 1_200, "completion_tokens": 100},
        # The next request has already been compacted before the provider counts it.
        {"prompt_tokens": 300, "completion_tokens": 40},
    ])

    assert usage == ConversationTokenUsage(input_tokens=1_500, output_tokens=140)


def test_missing_or_invalid_counts_are_zero_without_using_total_tokens() -> None:
    usage = ConversationTokenUsage.from_llm_usages([
        {"total_tokens": 999},
        {"prompt_tokens": -1, "completion_tokens": True},
        {"input_tokens": "12", "output_tokens": 3},
    ])

    assert usage == ConversationTokenUsage(input_tokens=0, output_tokens=3)
