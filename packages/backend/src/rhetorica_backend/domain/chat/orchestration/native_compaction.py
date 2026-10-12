"""Keep the provider's latest native checkpoint and its subsequent input."""

from typing import Any

from pydantic import BaseModel


def native_compaction_suffix(items: list[Any]) -> list[Any]:
    latest = -1
    for index, item in enumerate(items):
        value = item.model_dump(mode="json") if isinstance(item, BaseModel) else item
        if isinstance(value, dict) and value.get("type") == "compaction":
            latest = index
    return items[latest:] if latest >= 0 else items
