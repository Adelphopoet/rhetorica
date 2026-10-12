"""Coding receipts use the same provider normalization as ordinary chat."""

from dataclasses import asdict

from rhetorica_backend.domain.chat.orchestration.provider_usage import (
    inclusive_input_tokens,
    normalize_cache_usage,
)
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage

from agents.items import ModelResponse


def model_usage(response: ModelResponse, *, usage_format: str = "openai") -> dict:
    usage = response.usage
    cache = normalize_cache_usage(response.raw_usage)
    cached = int(getattr(usage.input_tokens_details, "cached_tokens", 0) or 0)
    write = int(getattr(usage.input_tokens_details, "cache_write_tokens", 0) or 0)
    counters = ConversationTokenUsage(
        input_tokens=inclusive_input_tokens(
            response.raw_usage, sdk_input=int(usage.input_tokens or 0), usage_format=usage_format
        ),
        output_tokens=int(usage.output_tokens or 0),
        cached_input_tokens=cache.read_tokens if cache.reported else cached,
        cache_write_input_tokens=cache.write_tokens if cache.write_reported else write,
        cache_usage_reported_calls=int(cache.reported or bool(cached)),
    )
    return {
        **asdict(counters),
        "total_tokens": counters.total_tokens,
        "usage_reported": response.raw_usage is not None
        or bool(usage.input_tokens or usage.output_tokens or usage.total_tokens),
        "response_id": response.response_id,
        "provider_request_id": response.request_id,
    }
