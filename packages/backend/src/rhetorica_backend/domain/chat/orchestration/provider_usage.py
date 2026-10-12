"""Normalize explicit provider cache counters without treating zero as missing."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CacheUsage:
    read_tokens: int = 0
    write_tokens: int = 0
    reported: bool = False
    write_reported: bool = False


def inclusive_input_tokens(raw: Any, *, sdk_input: int, usage_format: str) -> int:
    """Anthropic raw input excludes its separately reported cache counters."""
    if usage_format == "anthropic" and isinstance(raw, dict) and "input_tokens" in raw:
        cache = normalize_cache_usage(raw)
        return int(raw["input_tokens"] or 0) + cache.read_tokens + cache.write_tokens
    return sdk_input


def normalize_cache_usage(raw: Any) -> CacheUsage:
    """SDK raw usage is authoritative; synthetic SDK defaults are not evidence."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(exclude_unset=True)
    if not isinstance(raw, dict):
        return CacheUsage()
    read = write = None
    for name in ("input_tokens_details", "prompt_tokens_details"):
        details = raw.get(name)
        if isinstance(details, dict):
            read = details.get("cached_tokens", read)
            write = details.get("cache_write_tokens", write)
    for name in ("prompt_cache_hit_tokens", "cache_read_input_tokens"):
        if raw.get(name) is not None:
            read = raw[name]
    if raw.get("cache_creation_input_tokens") is not None:
        write = raw["cache_creation_input_tokens"]
    return CacheUsage(
        read_tokens=max(0, int(read or 0)),
        write_tokens=max(0, int(write or 0)),
        # Cache creation alone says nothing about cache hits on this request.
        reported=read is not None,
        write_reported=write is not None,
    )
