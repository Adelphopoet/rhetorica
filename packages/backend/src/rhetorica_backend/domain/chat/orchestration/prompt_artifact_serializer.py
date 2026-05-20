"""Typed prompt-facing serialization for runtime artifacts."""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from rhetorica_backend.domain.chat.llm.token_budget import LlmTokenEstimator
from rhetorica_backend.domain.chat.sql_prompt_window import (
    DEFAULT_SQL_PROMPT_ROWS,
    MAX_SQL_PROMPT_ROWS,
    normalize_sql_prompt_row_limit,
)

PromptArtifactType = Literal[
    "analysis_plan",
    "worker_result",
    "checkpoint_digest",
    "evidence_summary",
]


class PromptArtifactEnvelope(BaseModel):
    """Only artifact shape allowed to enter LLM messages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    artifact_type: PromptArtifactType
    compact_payload: dict[str, Any] = Field(default_factory=dict)
    token_estimate: int
    char_estimate: int
    truncated: bool = False
    truncation_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def stable_json(self) -> str:
        """Return deterministic JSON for prompt injection and snapshot tests."""
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )


class RunSqlPromptPayload(BaseModel):
    """LLM-facing SQL execution payload; audit SQL/debug stays outside prompts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    execution_status: str
    execution_id: str | None = None
    row_count: int | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)
    prompt_row_limit: int = 100
    max_prompt_row_limit: int = 500
    prompt_rows_returned: int = 0
    result_row_limit: int | None = None
    rows_truncated: bool = False
    retrieval_contract: Literal["bounded_prompt_preview"] = "bounded_prompt_preview"
    result_state: Literal["has_rows", "empty_result", "failed", "blocked", "unknown"]
    error_code: str | None = None
    error_message: str | None = None
    user_hint: str | None = None
    next_action_hint: str | None = None
    can_continue: bool = True


class PromptArtifactDiagnostics(BaseModel):
    """Compact prompt artifact diagnostics for dev LLM-content logging."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: PromptArtifactType
    tool_name: str | None = None
    original_row_count: int | None = None
    rows_sent: int | None = None
    rows_truncated: bool = False
    outer_json_truncated: bool = False
    char_estimate: int
    token_estimate: int
    truncation_flags: list[str] = Field(default_factory=list)


class PromptContextBudgetPolicy:
    """Prompt budget limits for compact runtime artifacts."""

    _MAX_PAYLOAD_CHARS = 40_000
    _MAX_TEXT_CHARS = 500
    _MAX_ITEMS = 8
    _MAX_ROWS = DEFAULT_SQL_PROMPT_ROWS
    _MAX_COLUMNS = 20
    _MAX_SCHEMA_COLUMNS = 200
    _MAX_TOKENS = 16_000

    def __init__(self, *, max_rows: int | None = None) -> None:
        self._max_rows = normalize_sql_prompt_row_limit(max_rows)

    @property
    def max_payload_chars(self) -> int:
        return self._MAX_PAYLOAD_CHARS

    @property
    def max_text_chars(self) -> int:
        return self._MAX_TEXT_CHARS

    @property
    def max_items(self) -> int:
        return self._MAX_ITEMS

    @property
    def max_rows(self) -> int:
        return self._max_rows

    @property
    def max_sql_prompt_rows(self) -> int:
        return MAX_SQL_PROMPT_ROWS

    @property
    def max_columns(self) -> int:
        return self._MAX_COLUMNS

    @property
    def max_schema_columns(self) -> int:
        return self._MAX_SCHEMA_COLUMNS

    @property
    def max_tokens(self) -> int:
        return self._MAX_TOKENS


class PromptArtifactSerializer:
    """Builds typed compact artifacts; raw runtime payloads stay outside prompts."""

    _ARTIFACT_FIELDS: ClassVar[dict[PromptArtifactType, tuple[str, ...]]] = {
        "analysis_plan": (
            "goal",
            "status",
            "steps",
            "required_evidence",
            "open_questions",
            "warnings",
        ),
        "worker_result": (
            "task_id",
            "goal",
            "status",
            "key_finding",
            "evidence_ids",
            "blocker",
            "next_action",
            "warnings",
        ),
        "checkpoint_digest": (
            "run_id",
            "plan_id",
            "status",
            "active_step_id",
            "completed_step_ids",
            "blocked_step_id",
            "budget_state",
            "evidence_ids",
            "blocker",
            "next_action",
            "warnings",
        ),
        "evidence_summary": (
            "tool_name",
            "run_sql",
            "ok",
            "count",
            "execution_id",
            "status",
            "execution_status",
            "result_state",
            "row_count",
            "columns",
            "rows",
            "rows_truncated",
            "tables",
            "candidates",
            "results",
            "items",
            "pages",
            "lineage",
            "entity",
            "columnNames",
            "evidence_ids",
            "claims",
            "warnings",
            "error_code",
            "error_message",
            "message",
            "user_hint",
            "can_continue",
            "issue_key",
            "url",
            "project_key",
            "assignee",
            "issue_type",
        ),
    }
    _FORBIDDEN_KEYS: ClassVar[frozenset[str]] = frozenset({
        "raw_payload",
        "raw_result",
        "raw_transcript",
        "transcript",
        "messages",
        "tool_calls",
        "tool_results",
        "debug",
        "debug_payload",
        "logs",
        "optimizer_trace",
        "execution_preview",
    })
    _ROW_LIST_KEYS: ClassVar[frozenset[str]] = frozenset({"rows", "data", "preview"})
    _ENTITY_LIST_KEYS: ClassVar[frozenset[str]] = frozenset({
        "tables",
        "results",
        "candidates",
        "items",
    })
    _COLUMN_KEYS: ClassVar[frozenset[str]] = frozenset({"columns", "columnNames"})
    _ENTITY_KEY_PRIORITY: ClassVar[tuple[str, ...]] = (
        "ok",
        "execution_status",
        "execution_id",
        "row_count",
        "columns",
        "rows",
        "prompt_row_limit",
        "max_prompt_row_limit",
        "prompt_rows_returned",
        "result_row_limit",
        "rows_truncated",
        "retrieval_contract",
        "result_state",
        "error_code",
        "error_message",
        "user_hint",
        "next_action_hint",
        "can_continue",
        "issue_key",
        "url",
        "project_key",
        "assignee",
        "issue_type",
        "entityType",
        "source",
        "fqn",
        "fullyQualifiedName",
        "name",
        "displayName",
        "service",
        "database",
        "schema",
        "databaseSchema",
        "description",
        "columns",
        "columnNames",
        "relevance_score",
        "matched_on",
        "reason_codes",
        "suspicious",
        "usage_count",
        "usage_frequency",
        "tier",
        "certification",
        "owners",
        "tags",
        "domains",
        "entity",
    )

    def __init__(
        self,
        *,
        budget_policy: PromptContextBudgetPolicy | None = None,
        estimator: LlmTokenEstimator | None = None,
    ) -> None:
        self._budget_policy = budget_policy or PromptContextBudgetPolicy()
        self._estimator = estimator or LlmTokenEstimator(model_name=None)

    def serialize(
        self,
        *,
        artifact_type: PromptArtifactType,
        artifact: dict[str, Any],
    ) -> PromptArtifactEnvelope:
        """Return a typed prompt-facing artifact envelope."""
        warnings: list[str] = []
        truncation_flags: list[str] = []
        artifact = self._normalize_artifact(artifact_type=artifact_type, artifact=artifact)
        compact_payload = self._select_payload(
            artifact_type=artifact_type,
            artifact=artifact,
            warnings=warnings,
            truncation_flags=truncation_flags,
        )
        compact_payload = self._fit_payload_chars(
            payload=compact_payload,
            warnings=warnings,
            truncation_flags=truncation_flags,
        )
        payload_chars = self._json_char_count(compact_payload)
        token_estimate = self._estimator.count_json(compact_payload)
        if token_estimate > self._budget_policy.max_tokens:
            self._append_once(warnings, "prompt_artifact_budget_warning")
        return PromptArtifactEnvelope(
            artifact_type=artifact_type,
            compact_payload=compact_payload,
            token_estimate=token_estimate,
            char_estimate=payload_chars,
            truncated=bool(truncation_flags),
            truncation_flags=truncation_flags,
            warnings=warnings,
        )

    def diagnostics(
        self,
        *,
        envelope: PromptArtifactEnvelope,
    ) -> PromptArtifactDiagnostics:
        """Return compact diagnostics for prompt artifact logging."""
        payload = envelope.compact_payload
        run_sql = payload.get("run_sql")
        rows_sent: int | None = None
        original_row_count: int | None = None
        rows_truncated = False
        if isinstance(run_sql, dict):
            rows = run_sql.get("rows")
            rows_sent = len(rows) if isinstance(rows, list) else 0
            row_count = run_sql.get("row_count")
            original_row_count = row_count if isinstance(row_count, int) else None
            rows_truncated = run_sql.get("rows_truncated") is True
        return PromptArtifactDiagnostics(
            artifact_type=envelope.artifact_type,
            tool_name=(
                payload.get("tool_name")
                if isinstance(payload.get("tool_name"), str)
                else None
            ),
            original_row_count=original_row_count,
            rows_sent=rows_sent,
            rows_truncated=rows_truncated,
            outer_json_truncated="artifact_payload_truncated" in envelope.truncation_flags,
            char_estimate=envelope.char_estimate,
            token_estimate=envelope.token_estimate,
            truncation_flags=list(envelope.truncation_flags),
        )

    def _normalize_artifact(
        self,
        *,
        artifact_type: PromptArtifactType,
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        if artifact_type != "evidence_summary":
            return artifact
        if artifact.get("tool_name") == "run_sql":
            return self._normalize_run_sql_artifact(artifact)
        return artifact

    def _normalize_run_sql_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(artifact)
        execution = normalized.get("execution")
        payload = self._run_sql_prompt_payload(normalized, execution)
        normalized["run_sql"] = payload.model_dump(mode="json", exclude_none=True)
        for raw_key in (
            "execution",
            "execution_id",
            "execution_status",
            "row_count",
            "columns",
            "rows",
            "result_state",
            "error_code",
            "error_message",
            "user_hint",
            "can_continue",
            "prompt_row_limit",
            "requested_prompt_row_limit",
        ):
            normalized.pop(raw_key, None)
        return normalized

    def _run_sql_prompt_payload(
        self,
        artifact: dict[str, Any],
        execution: Any,
    ) -> RunSqlPromptPayload:
        execution_payload = execution if isinstance(execution, dict) else {}
        preview = execution_payload.get("preview")
        preview_payload = preview if isinstance(preview, dict) else {}
        status = self._status_from_payload(artifact=artifact, execution=execution_payload)
        row_count = self._row_count_from_payload(artifact=artifact, execution=execution_payload)
        prompt_row_limit = self._prompt_row_limit_from_payload(artifact)
        result_row_limit = self._non_negative_int(execution_payload.get("applied_row_limit"))
        columns = self._string_list(preview_payload.get("columns") or artifact.get("columns"))
        raw_rows = preview_payload.get("rows") or artifact.get("rows")
        rows = raw_rows if isinstance(raw_rows, list) else []
        rows_sent = rows[:prompt_row_limit]
        rows_truncated = len(rows) > len(rows_sent)
        if row_count is not None and row_count > len(rows_sent):
            rows_truncated = True
        failed = status == "failed" or artifact.get("ok") is False
        result_state = self._result_state(
            status=status,
            failed=failed,
            row_count=row_count,
            rows_sent=rows_sent,
        )
        error_message = execution_payload.get("error_message") or artifact.get("error_message")
        user_hint = artifact.get("user_hint")
        if rows_truncated and not isinstance(user_hint, str):
            user_hint = (
                "Query succeeded; rows are a bounded prompt window, not a full export. "
                "Use prompt_row_limit or deterministic SQL pagination when more detail rows are required."
            )
        next_action_hint = None
        if rows_truncated:
            next_action_hint = (
                "If the window is insufficient, either rerun with prompt_row_limit up to "
                f"{self._budget_policy.max_sql_prompt_rows}, run an answer-ready aggregate SQL query, "
                "or use stable ORDER BY with LIMIT/OFFSET for deterministic pagination."
            )
        if result_state == "empty_result" and not isinstance(user_hint, str):
            user_hint = "Query succeeded but returned no rows."
        return RunSqlPromptPayload(
            ok=not failed,
            execution_status=status,
            execution_id=self._optional_str(
                execution_payload.get("execution_id") or artifact.get("execution_id")
            ),
            row_count=row_count,
            columns=columns[: self._budget_policy.max_columns],
            rows=rows_sent,
            prompt_row_limit=prompt_row_limit,
            max_prompt_row_limit=self._budget_policy.max_sql_prompt_rows,
            prompt_rows_returned=len(rows_sent),
            result_row_limit=result_row_limit,
            rows_truncated=rows_truncated,
            result_state=result_state,
            error_code=self._optional_str(artifact.get("error_code")),
            error_message=self._optional_str(error_message),
            user_hint=self._optional_str(user_hint),
            next_action_hint=next_action_hint,
            can_continue=bool(artifact.get("can_continue", True)),
        )

    def _prompt_row_limit_from_payload(self, artifact: dict[str, Any]) -> int:
        requested = artifact.get("prompt_row_limit")
        if requested is None:
            requested = artifact.get("requested_prompt_row_limit")
        return min(
            normalize_sql_prompt_row_limit(requested),
            self._budget_policy.max_sql_prompt_rows,
        )

    @staticmethod
    def _status_from_payload(
        *,
        artifact: dict[str, Any],
        execution: dict[str, Any],
    ) -> str:
        status = execution.get("status") or artifact.get("execution_status") or artifact.get("status")
        return status if isinstance(status, str) and status.strip() else "unknown"

    @staticmethod
    def _row_count_from_payload(
        *,
        artifact: dict[str, Any],
        execution: dict[str, Any],
    ) -> int | None:
        value = execution.get("row_count")
        if value is None:
            value = artifact.get("row_count")
        return value if isinstance(value, int) and value >= 0 else None

    @staticmethod
    def _non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _result_state(
        *,
        status: str,
        failed: bool,
        row_count: int | None,
        rows_sent: list[Any],
    ) -> Literal["has_rows", "empty_result", "failed", "blocked", "unknown"]:
        if failed:
            return "failed"
        if status == "blocked":
            return "blocked"
        if status == "succeeded":
            if row_count == 0:
                return "empty_result"
            if row_count is not None and row_count > 0:
                return "has_rows"
            return "has_rows" if rows_sent else "empty_result"
        return "unknown"

    @staticmethod
    def _copy_if_absent(
        target: dict[str, Any],
        source: dict[str, Any],
        key: str,
    ) -> None:
        value = source.get(key)
        if value is not None:
            target.setdefault(key, value)

    def _select_payload(
        self,
        *,
        artifact_type: PromptArtifactType,
        artifact: dict[str, Any],
        warnings: list[str],
        truncation_flags: list[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in self._ARTIFACT_FIELDS[artifact_type]:
            if key in artifact:
                payload[key] = self._compact_value(
                    value=artifact[key],
                    key=key,
                    truncation_flags=truncation_flags,
                )
        forbidden_present = sorted(key for key in artifact if key in self._FORBIDDEN_KEYS)
        if forbidden_present:
            self._append_once(
                warnings,
                f"prompt_{artifact_type}_raw_payload_omitted",
            )
        omitted_keys = len(set(artifact) - set(self._ARTIFACT_FIELDS[artifact_type]))
        if omitted_keys > len(forbidden_present):
            payload["_omitted_keys"] = omitted_keys
        return payload

    def _compact_value(
        self,
        *,
        value: Any,
        key: str,
        truncation_flags: list[str],
    ) -> Any:
        if isinstance(value, dict):
            if key == "run_sql" and value.get("retrieval_contract") == "bounded_prompt_preview":
                return self._compact_run_sql_dict(
                    value=value,
                    truncation_flags=truncation_flags,
                )
            return self._compact_dict(
                value=value,
                truncation_flags=truncation_flags,
            )
        if isinstance(value, list):
            return self._compact_list(
                value=value,
                key=key,
                truncation_flags=truncation_flags,
            )
        if isinstance(value, str):
            return self._compact_text(
                value=value,
                key=key,
                truncation_flags=truncation_flags,
            )
        return value

    def _compact_run_sql_dict(
        self,
        *,
        value: dict[str, Any],
        truncation_flags: list[str],
    ) -> dict[str, Any]:
        compacted: dict[str, Any] = {}
        selected_keys = list(value)[: self._budget_policy.max_columns]
        for key in selected_keys:
            if key in self._FORBIDDEN_KEYS:
                continue
            if key == "rows" and isinstance(value[key], list):
                compacted[key] = [
                    self._compact_sql_row(row, truncation_flags=truncation_flags)
                    for row in value[key]
                ]
                continue
            compacted[key] = self._compact_value(
                value=value[key],
                key=key,
                truncation_flags=truncation_flags,
            )
        skipped = len(value) - len(selected_keys)
        if skipped > 0:
            compacted["_omitted_keys"] = skipped
            self._append_once(truncation_flags, "object_keys_truncated")
        self._sync_run_sql_row_counters(compacted)
        return compacted

    def _compact_sql_row(
        self,
        row: Any,
        *,
        truncation_flags: list[str],
    ) -> Any:
        if isinstance(row, list):
            if len(row) > self._budget_policy.max_columns:
                self._append_once(truncation_flags, "row_columns_truncated")
            return [
                self._compact_value(
                    value=value,
                    key="value",
                    truncation_flags=truncation_flags,
                )
                for value in row[: self._budget_policy.max_columns]
            ]
        if isinstance(row, dict):
            return self._compact_dict(value=row, truncation_flags=truncation_flags)
        return self._compact_value(
            value=row,
            key="value",
            truncation_flags=truncation_flags,
        )

    def _compact_dict(
        self,
        *,
        value: dict[str, Any],
        truncation_flags: list[str],
    ) -> dict[str, Any]:
        compacted: dict[str, Any] = {}
        selected_keys = list(value)[: self._budget_policy.max_columns]
        for key in selected_keys:
            if key in self._FORBIDDEN_KEYS:
                continue
            compacted[key] = self._compact_value(
                value=value[key],
                key=key,
                truncation_flags=truncation_flags,
            )
        skipped = len(value) - len(selected_keys)
        if skipped > 0:
            compacted["_omitted_keys"] = skipped
            self._append_once(truncation_flags, "object_keys_truncated")
        return compacted

    def _compact_list(
        self,
        *,
        value: list[Any],
        key: str,
        truncation_flags: list[str],
    ) -> list[Any] | dict[str, Any]:
        if key in self._ROW_LIST_KEYS:
            limit = self._budget_policy.max_rows
        elif key in self._COLUMN_KEYS:
            limit = self._budget_policy.max_schema_columns
        else:
            limit = self._budget_policy.max_items
        items = [
            self._compact_value(value=item, key=key, truncation_flags=truncation_flags)
            for item in value[:limit]
        ]
        if len(value) <= limit:
            return items
        self._append_once(truncation_flags, f"{key}_truncated")
        return {
            "items": items,
            "returned_count": len(value),
            "truncated": True,
        }

    def _compact_text(
        self,
        *,
        value: str,
        key: str,
        truncation_flags: list[str],
    ) -> str:
        if len(value) <= self._budget_policy.max_text_chars:
            return value
        self._append_once(truncation_flags, f"{key}_truncated")
        return f"{value[: self._budget_policy.max_text_chars]}...[truncated]"

    def _fit_payload_chars(
        self,
        *,
        payload: dict[str, Any],
        warnings: list[str],
        truncation_flags: list[str],
    ) -> dict[str, Any]:
        if self._json_char_count(payload) <= self._budget_policy.max_payload_chars:
            return payload
        self._append_once(truncation_flags, "artifact_payload_truncated")
        self._append_once(warnings, "prompt_artifact_budget_warning")
        for item_limit, column_limit, text_limit in (
            (5, 12, 240),
            (3, 8, 160),
            (1, 6, 120),
        ):
            fitted = self._compact_oversized_payload(
                payload=payload,
                item_limit=item_limit,
                column_limit=column_limit,
                text_limit=text_limit,
            )
            if self._json_char_count(fitted) <= self._budget_policy.max_payload_chars:
                return fitted
        return self._summary_fallback_payload(payload)

    def _compact_oversized_payload(
        self,
        *,
        payload: dict[str, Any],
        item_limit: int,
        column_limit: int,
        text_limit: int,
    ) -> dict[str, Any]:
        return {
            key: self._compact_oversized_value(
                value=value,
                key=key,
                item_limit=item_limit,
                column_limit=column_limit,
                text_limit=text_limit,
            )
            for key, value in payload.items()
        }

    def _compact_oversized_value(
        self,
        *,
        value: Any,
        key: str,
        item_limit: int,
        column_limit: int,
        text_limit: int,
    ) -> Any:
        if (
            key in self._ENTITY_LIST_KEYS.union(self._COLUMN_KEYS)
            and isinstance(value, dict)
            and isinstance(value.get("items"), list)
        ):
            limit = column_limit if key in self._COLUMN_KEYS else item_limit
            compacted = {
                "items": [
                    self._compact_oversized_value(
                        value=item,
                        key=key,
                        item_limit=item_limit,
                        column_limit=column_limit,
                        text_limit=text_limit,
                    )
                    for item in value["items"][:limit]
                ]
            }
            for marker in ("returned_count", "truncated"):
                if marker in value:
                    compacted[marker] = value[marker]
            return compacted
        if isinstance(value, dict):
            return self._compact_oversized_entity(
                value=value,
                item_limit=item_limit,
                column_limit=column_limit,
                text_limit=text_limit,
            )
        if isinstance(value, list):
            limit = column_limit if key in self._COLUMN_KEYS else item_limit
            if key in self._ENTITY_LIST_KEYS or any(isinstance(item, dict) for item in value):
                return [
                    self._compact_oversized_value(
                        value=item,
                        key=key,
                        item_limit=item_limit,
                        column_limit=column_limit,
                        text_limit=text_limit,
                    )
                    for item in value[:limit]
                ]
            return value[:limit]
        if isinstance(value, str) and len(value) > text_limit:
            return f"{value[:text_limit]}...[truncated]"
        return value

    def _compact_oversized_entity(
        self,
        *,
        value: dict[str, Any],
        item_limit: int,
        column_limit: int,
        text_limit: int,
    ) -> dict[str, Any]:
        compacted: dict[str, Any] = {}
        ordered_keys = [
            key for key in self._ENTITY_KEY_PRIORITY if key in value and key not in self._FORBIDDEN_KEYS
        ]
        ordered_keys.extend(
            key
            for key in value
            if key not in compacted
            and key not in ordered_keys
            and key not in self._FORBIDDEN_KEYS
            and not isinstance(value[key], dict | list)
        )
        for key in ordered_keys[: self._budget_policy.max_columns]:
            compacted[key] = self._compact_oversized_value(
                value=value[key],
                key=key,
                item_limit=item_limit,
                column_limit=column_limit,
                text_limit=text_limit,
            )
        self._sync_run_sql_row_counters(compacted)
        return compacted

    @staticmethod
    def _sync_run_sql_row_counters(value: dict[str, Any]) -> None:
        if value.get("retrieval_contract") != "bounded_prompt_preview":
            return
        rows = value.get("rows")
        if not isinstance(rows, list):
            if "prompt_rows_returned" in value:
                value["prompt_rows_returned"] = 0
            return
        value["prompt_rows_returned"] = len(rows)
        row_count = value.get("row_count")
        if isinstance(row_count, int) and row_count > len(rows):
            value["rows_truncated"] = True

    def _summary_fallback_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        fallback = {
            key: value
            for key, value in payload.items()
            if isinstance(value, str | int | float | bool) or value is None
        }
        used_chars = self._json_char_count(fallback)
        summary_limit = max(0, self._budget_policy.max_payload_chars - used_chars - 32)
        fallback["summary"] = self._stable_payload_json(payload)[:summary_limit]
        return fallback

    @staticmethod
    def _stable_payload_json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _json_char_count(cls, payload: dict[str, Any]) -> int:
        return len(cls._stable_payload_json(payload))

    @staticmethod
    def _append_once(items: list[str], item: str) -> None:
        if item not in items:
            items.append(item)
