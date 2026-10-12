"""Bounded record projections; source order and opaque identifiers stay exact."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar


class RecordPreview(dict[str, Any]):
    """Internal marker, so external JSON cannot impersonate a generated page."""


@dataclass(frozen=True)
class RecordPreviewPolicy:
    """Project declared result lists, without ranking or selecting business meaning."""

    max_chars: int = 5_000
    max_items: int = 50
    description_chars: int = 120
    ID_FIELDS: ClassVar[tuple[str, ...]] = (
        "id", "name", "key", "fqn", "fullyQualifiedName", "url", "path", "issue_key",
    )
    LABEL_FIELDS: ClassVar[tuple[str, ...]] = (
        "displayName", "title", "type", "entityType", "status", "deleted", "tagFQN",
    )
    REFERENCE_FIELDS: ClassVar[tuple[str, ...]] = ("owners", "domains", "tags", "tier")

    def project(self, records: list[Any]) -> RecordPreview | None:
        # Small responses need no projection: preserve their additional fields
        # and avoid a page wrapper that costs more than the original records.
        if len(records) <= self.max_items:
            raw_chars = 2
            for record in records:
                raw_chars += len(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + 1
                if raw_chars > self.max_chars:
                    break
            else:
                return None
        if not records or not all(
            isinstance(record, dict) and any(
                isinstance(record.get(key), str | int) for key in self.ID_FIELDS
            )
            for record in records
        ):
            return None
        items: list[dict[str, Any]] = []
        page = self._page(items, len(records))
        for record in records[: self.max_items]:
            candidate = self._record(record)
            candidate_page = self._page([*items, candidate], len(records))
            if len(json.dumps(candidate_page, ensure_ascii=False, separators=(",", ":"))) > self.max_chars:
                break
            items.append(candidate)
            page = candidate_page
        return page

    def _record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._identity(record)
        for field in ("description", "snippet"):
            value = record.get(field)
            if isinstance(value, str):
                result[field] = value[: self.description_chars]
        for field in self.REFERENCE_FIELDS:
            value = record.get(field)
            if isinstance(value, dict):
                result[field] = self._reference(value)
            elif isinstance(value, list):
                result[field] = [
                    self._reference(item) if isinstance(item, dict) else item
                    for item in value[:3]
                    if isinstance(item, dict | str | int | bool)
                ]
        return result

    @staticmethod
    def _reference(value: dict[str, Any]) -> dict[str, Any]:
        for key in ("fullyQualifiedName", "tagFQN", "name", "id"):
            if isinstance(value.get(key), str):
                return {key: value[key]}
        return {}

    def _identity(self, record: dict[str, Any]) -> dict[str, Any]:
        # Identifiers/labels are never shortened, including when a single record
        # cannot fit. The caller retains the full artifact for narrower reads.
        return {
            key: record[key]
            for key in (*self.ID_FIELDS, *self.LABEL_FIELDS)
            if key in record and isinstance(record[key], str | int | float | bool)
        }

    @staticmethod
    def _page(items: list[dict[str, Any]], source_count: int) -> RecordPreview:
        return RecordPreview({
            "preview_kind": "record_page",
            "items": items,
            "source_count": source_count,
            "shown_count": len(items),
            "items_omitted": source_count - len(items),
            "details_omitted": True,
            "next_offset": len(items) if 0 < len(items) < source_count else None,
            "read_hint": "Counts cover this returned batch only. Read exact fields from the full artifact; omitted details are not absent facts.",
        })

    @classmethod
    def shrink(cls, page: RecordPreview, max_items: int) -> RecordPreview:
        return cls._page(page["items"][:max_items], page["source_count"])
