"""Conversation-retained storage and deterministic readers for full tool results."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from rhetorica_backend.domain.chat.tool_artifacts import (
    ConversationToolArtifactRecord,
    ConversationToolArtifactStore,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResultArtifact:
    """Full tool result kept outside the prompt window."""

    artifact_id: str
    tool_name: str
    arguments: dict[str, Any]
    payload: dict[str, Any]
    content_type: str
    char_count: int
    sha256: str
    observed_at: datetime


@dataclass
class ToolResultArtifactStore:
    """Turn-local artifact access backed by bounded conversation retention."""

    _artifacts: dict[str, ToolResultArtifact] = field(default_factory=dict)
    _artifact_ids_by_signature: dict[tuple[str, str, str], str] = field(default_factory=dict)
    _prompt_metadata_by_artifact_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    _durable_store: ConversationToolArtifactStore | None = None
    _conversation_id: str | None = None
    _retention: timedelta = timedelta(days=7)
    _max_durable_chars: int = 500_000
    _max_records: int = 100

    def configure_retention(
        self,
        *,
        durable_store: ConversationToolArtifactStore | None,
        conversation_id: str | None,
        ttl_hours: int = 168,
        max_durable_chars: int = 500_000,
        max_records: int = 100,
    ) -> None:
        """Attach deterministic conversation-scoped retention to this turn store."""
        self._durable_store = durable_store
        self._conversation_id = conversation_id
        self._retention = timedelta(hours=max(1, ttl_hours))
        self._max_durable_chars = max(1, max_durable_chars)
        self._max_records = max(1, max_records)

    def register(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a full tool result once and return stable prompt-safe metadata."""
        payload = copy.deepcopy(result)
        serialized = self._serialize(payload)
        payload = json.loads(serialized)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        signature = (
            tool_name,
            self._serialize(arguments),
            digest,
        )
        existing_artifact_id = self._artifact_ids_by_signature.get(signature)
        if existing_artifact_id is not None:
            existing_metadata = self._prompt_metadata_by_artifact_id.get(existing_artifact_id)
            if existing_metadata is not None:
                return copy.deepcopy(existing_metadata)

        artifact_id = f"ta_{uuid4().hex}"
        artifact = ToolResultArtifact(
            artifact_id=artifact_id,
            tool_name=tool_name,
            arguments=copy.deepcopy(arguments),
            payload=payload,
            content_type="application/json",
            char_count=len(serialized),
            sha256=digest,
            observed_at=datetime.now(tz=UTC),
        )
        self._artifacts[artifact_id] = artifact
        self._artifact_ids_by_signature[signature] = artifact_id
        prompt_metadata: dict[str, Any] = {
            "artifact_id": artifact_id,
            "tool_name": tool_name,
            "content_type": artifact.content_type,
            "char_count": artifact.char_count,
            "sha256": artifact.sha256,
        }
        if artifact.char_count > self._max_durable_chars:
            prompt_metadata.update({
                "durable": False,
                "durable_reason": "artifact_payload_too_large",
            })
            return self._retain_prompt_metadata(artifact_id, prompt_metadata)
        if self._durable_store is None or not self._conversation_id:
            prompt_metadata.update({
                "durable": False,
                "durable_reason": "conversation_artifact_store_unavailable",
            })
            return self._retain_prompt_metadata(artifact_id, prompt_metadata)
        expires_at = artifact.observed_at + self._retention
        try:
            self._durable_store.save_tool_artifact(
                conversation_id=self._conversation_id,
                artifact_id=artifact.artifact_id,
                tool_name=artifact.tool_name,
                arguments=artifact.arguments,
                payload=artifact.payload,
                content_sha256=artifact.sha256,
                source_char_count=artifact.char_count,
                observed_at=artifact.observed_at,
                expires_at=expires_at,
                max_records=self._max_records,
            )
        except Exception:
            logger.warning("Failed to retain conversation tool artifact", exc_info=True)
            prompt_metadata.update({
                "durable": False,
                "durable_reason": "conversation_artifact_persist_failed",
            })
            return self._retain_prompt_metadata(artifact_id, prompt_metadata)
        prompt_metadata.update({
            "durable": True,
            "expires_at": expires_at.isoformat(),
        })
        return self._retain_prompt_metadata(artifact_id, prompt_metadata)

    def _retain_prompt_metadata(
        self,
        artifact_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        retained = copy.deepcopy(metadata)
        self._prompt_metadata_by_artifact_id[artifact_id] = retained
        return copy.deepcopy(retained)

    def list_recent(self, *, limit: int = 20) -> dict[str, Any]:
        """List exact retained handles without injecting their raw payloads."""
        if self._durable_store is None or not self._conversation_id:
            return {
                "ok": False,
                "error_code": "conversation_artifact_store_unavailable",
                "can_continue": True,
            }
        bounded_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) else 20
        records = self._durable_store.list_tool_artifacts(
            conversation_id=self._conversation_id,
            limit=max(1, min(bounded_limit, 100)),
        )
        return {
            "ok": True,
            "count": len(records),
            "artifacts": [self._record_metadata(record) for record in records],
            "scope": "current_conversation",
        }

    def export(self, *, artifact_id: str) -> ToolResultArtifact | None:
        """Return an isolated full artifact for explicit durable retention."""
        artifact = self._get_artifact(artifact_id)
        return copy.deepcopy(artifact) if artifact is not None else None

    def inspect(self, *, artifact_id: str, max_paths: int = 200) -> dict[str, Any]:
        """Return a deterministic structural summary for an artifact."""
        artifact = self._get_artifact(artifact_id)
        if artifact is None:
            return self._not_found(artifact_id)
        bounded_max_paths = self._bounded_int(max_paths, minimum=1, maximum=1000)
        paths: list[dict[str, Any]] = []
        self._collect_paths(
            value=artifact.payload,
            pointer="",
            paths=paths,
            max_paths=bounded_max_paths,
        )
        paths_truncated = len(paths) >= bounded_max_paths
        while paths and len(self._serialize(paths)) > 12_000:
            paths.pop()
            paths_truncated = True
        return {
            "ok": True,
            "artifact_id": artifact.artifact_id,
            "tool_name": artifact.tool_name,
            "content_type": artifact.content_type,
            "char_count": artifact.char_count,
            "sha256": artifact.sha256,
            "paths": paths,
            "paths_truncated": paths_truncated,
        }

    def read(
        self,
        *,
        artifact_id: str,
        json_pointer: str | None = None,
        offset: int = 0,
        limit: int = 12_000,
        format: str = "text",  # noqa: A002 - matches the public artifact reader argument
        fields: list[str] | None = None,
        parse_json: bool = False,
    ) -> dict[str, Any]:
        """Read a deterministic serialized chunk from a full artifact or subpath."""
        artifact = self._get_artifact(artifact_id)
        if artifact is None:
            return self._not_found(artifact_id)
        selected: Any = artifact.payload
        resolved_pointer = json_pointer or ""
        if resolved_pointer:
            selected = self._select_json_pointer(artifact.payload, resolved_pointer)
            if isinstance(selected, _JsonPointerError):
                return {
                    "ok": False,
                    "error_code": "tool_artifact_path_not_found",
                    "user_message": selected.message,
                    "artifact_id": artifact_id,
                    "json_pointer": resolved_pointer,
                    "can_continue": True,
                }
        if format == "json":
            return self._read_json_page(
                artifact_id=artifact_id,
                selected=selected,
                json_pointer=resolved_pointer,
                fields=fields,
                offset=offset,
                limit=limit,
                parse_json=parse_json,
            )
        serialized = self._serialize(selected)
        bounded_offset = self._bounded_int(offset, minimum=0, maximum=len(serialized))
        bounded_limit = self._bounded_int(limit, minimum=1, maximum=40_000)
        end = min(len(serialized), bounded_offset + bounded_limit)
        while (
            end > bounded_offset + 1
            and len(self._serialize({"content": serialized[bounded_offset:end]})) > 12_000
        ):
            end = bounded_offset + max(1, (end - bounded_offset) // 2)
        return {
            "ok": True,
            "artifact_id": artifact.artifact_id,
            "tool_name": artifact.tool_name,
            "json_pointer": resolved_pointer,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "content": serialized[bounded_offset:end],
            "char_count": len(serialized),
            "next_offset": end if end < len(serialized) else None,
            "has_more": end < len(serialized),
        }

    def _read_json_page(
        self,
        *,
        artifact_id: str,
        selected: Any,
        json_pointer: str,
        fields: list[str] | None,
        offset: int,
        limit: int,
        parse_json: bool,
    ) -> dict[str, Any]:
        """Return complete JSON values, never a partially escaped JSON string."""
        if parse_json:
            for _ in range(3):
                if not isinstance(selected, str):
                    break
                try:
                    selected = json.loads(selected)
                except ValueError:
                    return {
                        "ok": False,
                        "error_code": "artifact_invalid_json",
                        "artifact_id": artifact_id,
                    }
        if fields is not None and (
            not isinstance(fields, list)
            or len(fields) > 20
            or any(not isinstance(key, str) for key in fields)
        ):
            return {
                "ok": False,
                "error_code": "artifact_invalid_fields",
                "artifact_id": artifact_id,
            }

        def project(value: Any) -> Any:
            if fields is not None and isinstance(value, dict):
                return {key: value[key] for key in fields if key in value}
            return value

        base: dict[str, Any] = {
            "ok": True,
            "artifact_id": artifact_id,
            "json_pointer": json_pointer,
            "format": "json",
            "has_more": False,
            "next_offset": None,
        }
        if isinstance(selected, str):
            start = self._bounded_int(offset, minimum=0, maximum=len(selected))
            count = self._bounded_int(limit, minimum=1, maximum=8_000)
            end = min(len(selected), start + count)
            while end > start + 1 and len(self._serialize({"data": selected[start:end]})) > 10_000:
                end = start + max(1, (end - start) // 2)
            return {
                **base,
                "data": selected[start:end],
                "offset": start,
                "offset_unit": "characters",
                "has_more": end < len(selected),
                "next_offset": end if end < len(selected) else None,
            }
        if isinstance(selected, list):
            start = self._bounded_int(offset, minimum=0, maximum=len(selected))
            count = self._bounded_int(limit, minimum=1, maximum=100)
            items: list[Any] = []
            base.update({
                "offset": start,
                "offset_unit": "items",
                "total_items": len(selected),
                "data": items,
            })
            for item in selected[start : start + count]:
                candidate = project(item)
                if len(self._serialize({**base, "data": [*items, candidate]})) > 10_000:
                    break
                items.append(candidate)
            if not items and start < len(selected):
                base.update({
                    "ok": False,
                    "error_code": "artifact_item_exceeds_page_budget",
                    "next_action_hint": "Select fewer fields or a deeper json_pointer.",
                })
            else:
                end = start + len(items)
                base.update({
                    "has_more": end < len(selected),
                    "next_offset": end if end < len(selected) else None,
                })
            return base
        data = project(selected)
        if len(self._serialize({"data": data})) > 10_000:
            return {
                **base,
                "ok": False,
                "error_code": "artifact_item_exceeds_page_budget",
                "next_action_hint": "Select fewer fields or a deeper json_pointer.",
            }
        return {**base, "data": data}

    def find_text(
        self,
        *,
        artifact_id: str,
        text: str,
        case_sensitive: bool = False,
        max_matches: int = 20,
        context_chars: int = 200,
    ) -> dict[str, Any]:
        """Find exact text occurrences in serialized artifact content."""
        artifact = self._get_artifact(artifact_id)
        if artifact is None:
            return self._not_found(artifact_id)
        needle = text if case_sensitive else text.lower()
        if not needle:
            return {
                "ok": False,
                "error_code": "tool_artifact_empty_search_text",
                "user_message": "Search text must be non-empty.",
                "artifact_id": artifact_id,
                "can_continue": True,
            }
        serialized = self._serialize(artifact.payload)
        haystack = serialized if case_sensitive else serialized.lower()
        bounded_max_matches = self._bounded_int(max_matches, minimum=1, maximum=100)
        bounded_context_chars = self._bounded_int(context_chars, minimum=0, maximum=1000)
        matches: list[dict[str, Any]] = []
        start = 0
        while len(matches) < bounded_max_matches:
            index = haystack.find(needle, start)
            if index < 0:
                break
            snippet_start = max(0, index - bounded_context_chars)
            snippet_end = min(len(serialized), index + len(needle) + bounded_context_chars)
            candidate = {
                "offset": index,
                "snippet": serialized[snippet_start:snippet_end],
            }
            if len(self._serialize([*matches, candidate])) > 12_000:
                break
            matches.append(candidate)
            start = index + max(1, len(needle))
        has_more = haystack.find(needle, start) >= 0
        return {
            "ok": True,
            "artifact_id": artifact.artifact_id,
            "tool_name": artifact.tool_name,
            "text": text,
            "case_sensitive": case_sensitive,
            "match_count": len(matches),
            "matches": matches,
            "has_more": has_more,
        }

    def _get_artifact(self, artifact_id: str) -> ToolResultArtifact | None:
        artifact = self._artifacts.get(artifact_id)
        if artifact is not None:
            return artifact
        if self._durable_store is None or not self._conversation_id:
            return None
        record = self._durable_store.get_tool_artifact(
            conversation_id=self._conversation_id,
            artifact_id=artifact_id,
        )
        if record is None:
            return None
        artifact = self._from_record(record)
        self._artifacts[artifact_id] = artifact
        return artifact

    @staticmethod
    def _from_record(record: ConversationToolArtifactRecord) -> ToolResultArtifact:
        return ToolResultArtifact(
            artifact_id=record.artifact_id,
            tool_name=record.tool_name,
            arguments=copy.deepcopy(record.arguments),
            payload=copy.deepcopy(record.payload),
            content_type="application/json",
            char_count=record.source_char_count,
            sha256=record.content_sha256,
            observed_at=record.observed_at,
        )

    @staticmethod
    def _record_metadata(record: ConversationToolArtifactRecord) -> dict[str, Any]:
        return {
            "artifact_id": record.artifact_id,
            "tool_name": record.tool_name,
            "arguments": record.arguments,
            "content_type": "application/json",
            "char_count": record.source_char_count,
            "sha256": record.content_sha256,
            "observed_at": record.observed_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
        }

    @staticmethod
    def _not_found(artifact_id: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": "tool_artifact_not_found",
            "user_message": (
                f"Tool artifact is unavailable or expired in this conversation: {artifact_id}."
            ),
            "next_action_hint": (
                "Call list_recent_tool_artifacts and use an exact ta_<uuid> artifact_id. "
                "Never pass execution_id."
            ),
            "artifact_id": artifact_id,
            "can_continue": True,
        }

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _collect_paths(
        cls,
        *,
        value: Any,
        pointer: str,
        paths: list[dict[str, Any]],
        max_paths: int,
    ) -> None:
        if len(paths) >= max_paths:
            return
        paths.append(cls._path_summary(value=value, pointer=pointer))
        if isinstance(value, dict):
            for key in sorted(value):
                cls._collect_paths(
                    value=value[key],
                    pointer=f"{pointer}/{cls._escape_pointer_token(str(key))}",
                    paths=paths,
                    max_paths=max_paths,
                )
                if len(paths) >= max_paths:
                    return
        if isinstance(value, list) and value:
            sample_count = min(len(value), 3)
            for index in range(sample_count):
                cls._collect_paths(
                    value=value[index],
                    pointer=f"{pointer}/{index}",
                    paths=paths,
                    max_paths=max_paths,
                )
                if len(paths) >= max_paths:
                    return

    @classmethod
    def _path_summary(cls, *, value: Any, pointer: str) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "json_pointer": pointer,
            "type": cls._json_type(value),
        }
        if isinstance(value, dict):
            keys = sorted(str(key) for key in value)
            summary["key_count"] = len(keys)
            summary["keys"] = keys[:40]
            summary["keys_truncated"] = len(keys) > 40
        elif isinstance(value, list):
            summary["length"] = len(value)
            if value:
                summary["sample_item_type"] = cls._json_type(value[0])
        elif isinstance(value, str):
            summary["char_count"] = len(value)
            summary["preview"] = value[:240]
            summary["preview_truncated"] = len(value) > 240
        return summary

    @staticmethod
    def _json_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int | float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return type(value).__name__

    @classmethod
    def _select_json_pointer(cls, value: Any, pointer: str) -> Any:
        if not pointer.startswith("/"):
            return _JsonPointerError("JSON Pointer must be empty or start with '/'.")
        current = value
        for raw_token in pointer.split("/")[1:]:
            token = cls._unescape_pointer_token(raw_token)
            if isinstance(current, dict):
                if token not in current:
                    return _JsonPointerError(f"JSON Pointer key not found: {token}.")
                current = current[token]
                continue
            if isinstance(current, list):
                if not token.isdigit():
                    return _JsonPointerError(f"JSON Pointer array index is invalid: {token}.")
                index = int(token)
                if index >= len(current):
                    return _JsonPointerError(f"JSON Pointer array index out of range: {token}.")
                current = current[index]
                continue
            return _JsonPointerError(f"JSON Pointer cannot descend into {type(current).__name__}.")
        return current

    @staticmethod
    def _escape_pointer_token(token: str) -> str:
        return token.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _unescape_pointer_token(token: str) -> str:
        return token.replace("~1", "/").replace("~0", "~")

    @staticmethod
    def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            return minimum
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return minimum
        return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class _JsonPointerError:
    message: str
