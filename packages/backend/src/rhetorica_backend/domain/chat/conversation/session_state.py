"""Deterministic semantic session state for multi-turn chat."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from rhetorica_backend.contracts.chat import (
    SemanticGrounding,
    SemanticGroundingCandidate,
)
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)

_RESET_PATTERNS = (
    "начнем заново",
    "начнём заново",
    "new chat",
    "start over",
    "reset context",
)

_METRIC_KEYWORDS = {
    "gmv",
    "revenue",
    "mau",
    "dau",
    "arpu",
    "retention",
    "churn",
    "orders",
    "margin",
    "aov",
    "ctr",
    "конверсия",
    "выручка",
    "маржа",
    "заказы",
}

_DIMENSION_KEYWORDS = {
    "country",
    "region",
    "segment",
    "product",
    "channel",
    "city",
    "category",
    "warehouse",
    "store",
    "страна",
    "регион",
    "сегмент",
    "категория",
    "город",
}

_TIME_RANGE_REGEXES = (
    re.compile(
        r"\b(?:за\s+)?(?:последн(?:ий|ие|юю)|прошл(?:ый|ую|ые))\s+\d+\s+"
        r"(?:дн(?:ей|я)|недел(?:и|ю)|месяц(?:а|ев)?|квартал(?:а|ов)?|год(?:а|ов)?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:за\s+)?(?:последн(?:ий|юю)|прошл(?:ый|ую))\s+"
        r"(?:день|неделю|месяц|квартал|год)\b",
        re.I,
    ),
    re.compile(r"\b(last|previous)\s+\d+\s+(day|days|week|weeks|month|months)\b", re.I),
    re.compile(r"\b(this|last|previous)\s+(week|month|quarter|year)\b", re.I),
    re.compile(
        r"\b(?:с|from)\s+\d{4}-\d{2}-\d{2}\s+(?:по|to|until)\s+\d{4}-\d{2}-\d{2}\b",
        re.I,
    ),
    re.compile(
        r"\bbetween\s+\d{4}-\d{2}-\d{2}\s+and\s+\d{4}-\d{2}-\d{2}\b",
        re.I,
    ),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:\u0437\u0430|\u0441|\u043f\u043e)\s+\d{4}\b", re.I),
    re.compile(r"\b(q[1-4]\s*\d{4})\b", re.I),
    re.compile(r"\b(?:сегодня|вчера|позавчера)\b", re.I),
)

_TIME_COLUMN_TOKENS = (
    "date",
    "day",
    "week",
    "month",
    "quarter",
    "year",
    "dt",
    "time",
    "дата",
    "неделя",
    "месяц",
    "квартал",
    "год",
)
_FOLLOWUP_HINTS = (
    "а теперь",
    "теперь",
    "тогда",
    "разбей",
    "разбивка",
    "добавь",
    "сравни",
    "compare",
    "breakdown",
    "break down",
    "only",
    "только",
    "без ",
)
_DIMENSION_CAPTURE_REGEXES = (
    re.compile(r"\b(?:по|by)\s+([^?.!,;\n]+)", re.I),
    re.compile(r"\b(?:разбивк[ае]\s+по|group by)\s+([^?.!,;\n]+)", re.I),
)
_DIMENSION_BOUNDARY_REGEX = re.compile(
    r"\b(?:за|с|from|только|only|where|где|кроме|без|for|для|when|если)\b",
    re.I,
)
_ENTITY_SCOPE_REGEXES = (
    re.compile(r"\b(?:только|only)\s+([^?.!,;\n]+)", re.I),
    re.compile(r"\b(?:без|except|excluding)\s+([^?.!,;\n]+)", re.I),
    re.compile(r"\b(?:для|for)\s+([^?.!,;\n]+)", re.I),
    re.compile(r"\b(?:в\s+сегменте|in\s+segment)\s+([^?.!,;\n]+)", re.I),
)
_ENTITY_SCOPE_BOUNDARY_REGEX = re.compile(
    r"\b(?:за|с|from|по|by|where|где|when|если)\b",
    re.I,
)


@dataclass
class SessionState:
    """Structured session state for prompt digest."""

    active_metrics: list[str] = field(default_factory=list)
    active_dimensions: list[str] = field(default_factory=list)
    time_range: str | None = None
    entity_scope: list[str] = field(default_factory=list)
    selected_connector: str | None = None
    confirmed_assumptions: list[str] = field(default_factory=list)
    open_ambiguities: list[str] = field(default_factory=list)
    last_successful_execution_id: str | None = None
    state_revision: int = 0
    transitions: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """Serialize persisted conversation state."""
        return {
            "active_metrics": list(self.active_metrics),
            "active_dimensions": list(self.active_dimensions),
            "time_range": self.time_range,
            "entity_scope": list(self.entity_scope),
            "selected_connector": self.selected_connector,
            "confirmed_assumptions": list(self.confirmed_assumptions),
            "open_ambiguities": list(self.open_ambiguities),
            "last_successful_execution_id": self.last_successful_execution_id,
            "state_revision": self.state_revision,
            "transitions": list(self.transitions),
        }


@dataclass
class RuntimeContext:
    """Runtime context restored from compacted conversation history."""

    recent_tables: list[str] = field(default_factory=list)
    metadata_table_columns: dict[str, set[str]] = field(default_factory=dict)
    semantic_grounding: SemanticGrounding | None = None
    selected_connector: str | None = None
    active_metrics: list[str] = field(default_factory=list)
    active_dimensions: list[str] = field(default_factory=list)
    time_range: str | None = None
    entity_scope: list[str] = field(default_factory=list)
    confirmed_assumptions: list[str] = field(default_factory=list)
    open_ambiguities: list[str] = field(default_factory=list)


def build_session_state_from_messages(messages: list[Any]) -> SessionState:
    """Replay messages and build deterministic semantic session state."""
    state = SessionState()

    for msg in messages:
        role = str(getattr(msg, "role", "") or "")
        content = str(getattr(msg, "content", "") or "")
        if role == "user":
            if _contains_reset_command(content):
                state = SessionState()
                state.transitions.append("reset_by_user_command")
                continue
            _update_state_from_user_text(state, content)

        generated_sql = getattr(msg, "generated_sql", None)
        if isinstance(generated_sql, str) and generated_sql.strip():
            _update_state_from_sql(state, generated_sql.strip())

        selected_connector = getattr(msg, "selected_connector", None)
        if isinstance(selected_connector, str) and selected_connector.strip():
            state.selected_connector = selected_connector.strip()
            state.transitions.append("selected_connector_updated")

        execution_id = getattr(msg, "execution_id", None)
        warnings = getattr(msg, "warnings", None)
        if (
            isinstance(execution_id, str)
            and execution_id.strip()
            and not (
                isinstance(warnings, list)
                and "sql_execution_failed" in warnings
            )
        ):
            state.last_successful_execution_id = execution_id.strip()
            state.transitions.append("last_successful_execution_updated")

    return state


def contains_reset_command(content: str) -> bool:
    """Return whether user text explicitly resets semantic session state."""
    return _contains_reset_command(content)


def session_state_from_payload(payload: dict[str, Any] | None) -> SessionState:
    """Restore persisted conversation state from a JSON payload."""
    if not isinstance(payload, dict):
        return SessionState()
    return SessionState(
        active_metrics=_string_items(payload.get("active_metrics")),
        active_dimensions=_string_items(payload.get("active_dimensions")),
        time_range=_optional_str(payload.get("time_range")),
        entity_scope=_string_items(payload.get("entity_scope")),
        selected_connector=_optional_str(payload.get("selected_connector")),
        confirmed_assumptions=_string_items(payload.get("confirmed_assumptions")),
        open_ambiguities=_string_items(payload.get("open_ambiguities")),
        last_successful_execution_id=_optional_str(
            payload.get("last_successful_execution_id")
        ),
        state_revision=(
            payload["state_revision"]
            if isinstance(payload.get("state_revision"), int)
            else 0
        ),
        transitions=_string_items(payload.get("transitions")),
    )


def merge_session_states(
    *,
    persisted: SessionState,
    update: SessionState,
    increment_revision: bool = True,
) -> SessionState:
    """Merge deterministic turn updates into persisted semantic memory."""
    merged = SessionState(
        active_metrics=_merge_unique(persisted.active_metrics, update.active_metrics),
        active_dimensions=_merge_unique(
            persisted.active_dimensions,
            update.active_dimensions,
        ),
        time_range=update.time_range or persisted.time_range,
        entity_scope=_merge_unique(persisted.entity_scope, update.entity_scope),
        selected_connector=update.selected_connector or persisted.selected_connector,
        confirmed_assumptions=_merge_unique(
            persisted.confirmed_assumptions,
            update.confirmed_assumptions,
        ),
        open_ambiguities=_merge_unique(
            persisted.open_ambiguities,
            update.open_ambiguities,
        ),
        last_successful_execution_id=(
            update.last_successful_execution_id
            or persisted.last_successful_execution_id
        ),
        state_revision=(
            persisted.state_revision + 1
            if increment_revision
            else persisted.state_revision
        ),
        transitions=[
            *persisted.transitions[-20:],
            *update.transitions,
            *(["state_revision_incremented"] if increment_revision else []),
        ],
    )
    return merged


def extract_runtime_context_from_history(
    messages: list[dict[str, Any]] | None,
) -> RuntimeContext:
    """Restore recent tables and connector from compacted history digest."""
    context = RuntimeContext()
    semantic_candidates: list[SemanticGroundingCandidate] = []
    chosen_table_fqn: str | None = None
    selection_rationale: str | None = None
    lineage_summary: str | None = None
    if not messages:
        return context

    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or "Context digest:" not in content:
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if line.startswith("recent_tables="):
                tables = [
                    item.strip()
                    for item in line.removeprefix("recent_tables=").split(",")
                    if item.strip()
                ]
                for table in tables:
                    if table not in context.recent_tables:
                        context.recent_tables.append(table)
            elif line.startswith("table_columns."):
                table, separator, raw_columns = line.partition("=")
                if not separator:
                    continue
                table_name = table.removeprefix("table_columns.").strip()
                if not table_name:
                    continue
                normalized_table = table_name.lower()
                columns = {
                    column.strip().lower()
                    for column in raw_columns.split("|")
                    if column.strip()
                }
                if columns:
                    context.metadata_table_columns.setdefault(normalized_table, set()).update(
                        columns
                    )
            elif line.startswith("semantic_chosen_table="):
                chosen_table = line.removeprefix("semantic_chosen_table=").strip()
                if chosen_table:
                    chosen_table_fqn = chosen_table
            elif line.startswith("semantic_selection_rationale="):
                rationale = line.removeprefix(
                    "semantic_selection_rationale="
                ).strip()
                if rationale:
                    selection_rationale = rationale
            elif line.startswith("semantic_lineage_summary="):
                summary = line.removeprefix("semantic_lineage_summary=").strip()
                if summary:
                    lineage_summary = summary
            elif line.startswith("semantic_candidate="):
                raw_candidate = line.removeprefix("semantic_candidate=").strip()
                if not raw_candidate:
                    continue
                try:
                    candidate = SemanticGroundingCandidate.model_validate(
                        json.loads(raw_candidate)
                    )
                except Exception:
                    logger.debug("Failed to decode semantic grounding candidate", exc_info=True)
                    continue
                if not any(existing.fqn == candidate.fqn for existing in semantic_candidates):
                    semantic_candidates.append(candidate)
            elif line.startswith("selected_connector="):
                connector = line.removeprefix("selected_connector=").strip()
                if connector:
                    context.selected_connector = connector
            elif line.startswith("session_selected_connector="):
                connector = line.removeprefix(
                    "session_selected_connector="
                ).strip()
                if connector:
                    context.selected_connector = connector
            elif line.startswith("active_metrics="):
                context.active_metrics = _merge_unique(
                    context.active_metrics,
                    _split_digest_list(line.removeprefix("active_metrics=")),
                )
            elif line.startswith("active_dimensions="):
                context.active_dimensions = _merge_unique(
                    context.active_dimensions,
                    _split_digest_list(line.removeprefix("active_dimensions=")),
                )
            elif line.startswith("time_range="):
                time_range = line.removeprefix("time_range=").strip()
                if time_range:
                    context.time_range = time_range
            elif line.startswith("entity_scope="):
                context.entity_scope = _merge_unique(
                    context.entity_scope,
                    _split_digest_list(line.removeprefix("entity_scope=")),
                )
            elif line.startswith("confirmed_assumptions="):
                context.confirmed_assumptions = _merge_unique(
                    context.confirmed_assumptions,
                    [
                        item.strip()
                        for item in line.removeprefix("confirmed_assumptions=").split("|")
                        if item.strip()
                    ],
                )
            elif line.startswith("open_ambiguities="):
                context.open_ambiguities = _merge_unique(
                    context.open_ambiguities,
                    _split_digest_list(line.removeprefix("open_ambiguities=")),
                )

    if semantic_candidates or chosen_table_fqn or selection_rationale or lineage_summary:
        context.semantic_grounding = SemanticGrounding(
            chosen_table_fqn=chosen_table_fqn,
            candidates=semantic_candidates[:3],
            selection_rationale=selection_rationale,
            lineage_summary=lineage_summary,
        )
    return context


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*first, *second]:
        if item and item not in merged:
            merged.append(item)
    return merged


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _contains_reset_command(content: str) -> bool:
    normalized = content.strip().lower()
    return any(pattern in normalized for pattern in _RESET_PATTERNS)


def _update_state_from_user_text(state: SessionState, content: str) -> None:
    normalized = content.lower()
    extracted_metrics = _extract_metrics_from_user_text(content)
    if extracted_metrics:
        state.active_metrics = _merge_unique(state.active_metrics, extracted_metrics)
        state.transitions.append("metrics_updated_from_user")

    extracted_dimensions = _extract_dimensions_from_user_text(content)
    if extracted_dimensions:
        state.active_dimensions = _merge_unique(
            state.active_dimensions, extracted_dimensions
        )
        state.transitions.append("dimensions_updated_from_user")

    time_range = _extract_time_range(content)
    if time_range:
        state.time_range = time_range
        state.transitions.append("time_range_updated_from_user")

    extracted_entity_scope = _extract_entity_scope_from_user_text(content)
    if extracted_entity_scope:
        state.entity_scope = _merge_unique(state.entity_scope, extracted_entity_scope)
        state.transitions.append("entity_scope_updated_from_user")

    assumption_matches = re.findall(
        r"\b(?:предположим|допустим|assume)\b[^.?!]*",
        content,
        flags=re.IGNORECASE,
    )
    if assumption_matches:
        cleaned_assumptions = [
            item.strip() for item in assumption_matches if item.strip()
        ]
        state.confirmed_assumptions = _merge_unique(
            state.confirmed_assumptions,
            cleaned_assumptions,
        )
        state.transitions.append("assumptions_updated_from_user")

    if extracted_metrics:
        if "metric_not_explicit" in state.open_ambiguities:
            state.open_ambiguities = [
                item
                for item in state.open_ambiguities
                if item != "metric_not_explicit"
            ]
            state.transitions.append("ambiguity_resolved_from_user")
        return

    if _is_followup_refinement(
        normalized=normalized,
        state=state,
        extracted_dimensions=extracted_dimensions,
        extracted_entity_scope=extracted_entity_scope,
        time_range=time_range,
    ):
        if "metric_not_explicit" in state.open_ambiguities:
            state.open_ambiguities = [
                item
                for item in state.open_ambiguities
                if item != "metric_not_explicit"
            ]
            state.transitions.append("ambiguity_resolved_from_user")
        return

    if "?" in content:
        ambiguity = "metric_not_explicit"
        if ambiguity not in state.open_ambiguities:
            state.open_ambiguities.append(ambiguity)
            state.transitions.append("ambiguity_added_from_user")


def _update_state_from_sql(state: SessionState, sql: str) -> None:
    try:
        parsed = parse_one(sql)
    except ParseError:
        return

    metric_candidates = _extract_metrics_from_sql(parsed)
    dimension_candidates = _extract_dimensions_from_sql(parsed)

    if metric_candidates:
        state.active_metrics = _merge_unique(
            state.active_metrics, metric_candidates
        )
        state.transitions.append("metrics_updated_from_sql")
    if dimension_candidates:
        state.active_dimensions = _merge_unique(
            state.active_dimensions, dimension_candidates
        )
        state.transitions.append("dimensions_updated_from_sql")

    range_candidate = _extract_time_range_from_sql(parsed)
    if range_candidate:
        state.time_range = range_candidate
        state.transitions.append("time_range_updated_from_sql")

    entity_scope = _extract_entity_scope_from_sql(parsed)
    if entity_scope:
        state.entity_scope = _merge_unique(state.entity_scope, entity_scope)
        state.transitions.append("entity_scope_updated_from_sql")


def _extract_time_range(content: str) -> str | None:
    for pattern in _TIME_RANGE_REGEXES:
        match = pattern.search(content)
        if match:
            return match.group(0).strip()
    return None


def _extract_metrics_from_user_text(content: str) -> list[str]:
    normalized = content.lower()
    return sorted(keyword for keyword in _METRIC_KEYWORDS if keyword in normalized)


def _extract_dimensions_from_user_text(content: str) -> list[str]:
    normalized = content.lower()
    candidates = sorted(keyword for keyword in _DIMENSION_KEYWORDS if keyword in normalized)

    for pattern in _DIMENSION_CAPTURE_REGEXES:
        for match in pattern.findall(content):
            scoped = _DIMENSION_BOUNDARY_REGEX.split(match, maxsplit=1)[0]
            for item in _split_phrase_list(scoped):
                normalized_item = item.lower()
                if normalized_item and normalized_item not in candidates:
                    candidates.append(normalized_item)

    return candidates


def _extract_entity_scope_from_user_text(content: str) -> list[str]:
    extracted: list[str] = []
    for pattern in _ENTITY_SCOPE_REGEXES:
        for match in pattern.findall(content):
            scoped = _ENTITY_SCOPE_BOUNDARY_REGEX.split(match, maxsplit=1)[0]
            cleaned = _normalize_phrase(scoped)
            if not cleaned:
                continue
            normalized = cleaned.lower()
            if normalized in _DIMENSION_KEYWORDS or normalized in _METRIC_KEYWORDS:
                continue
            if normalized not in extracted:
                extracted.append(normalized)
    return extracted


def _extract_metrics_from_sql(parsed: exp.Expression) -> list[str]:
    metric_candidates: list[str] = []

    if isinstance(parsed, exp.Select):
        for projection in parsed.expressions:
            alias = projection.alias_or_name.lower()
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(expression, exp.AggFunc):
                metric_name = alias or expression.sql_name().lower()
                if metric_name not in metric_candidates:
                    metric_candidates.append(metric_name)
                continue
            if isinstance(expression, exp.Column) and _looks_like_metric_name(expression.name):
                normalized = expression.name.lower()
                if normalized not in metric_candidates:
                    metric_candidates.append(normalized)

    for column in parsed.find_all(exp.Column):
        name = column.name
        if name and _looks_like_metric_name(name):
            normalized = name.lower()
            if normalized not in metric_candidates:
                metric_candidates.append(normalized)

    return metric_candidates


def _extract_dimensions_from_sql(parsed: exp.Expression) -> list[str]:
    dimension_candidates: list[str] = []

    group = parsed.args.get("group")
    if isinstance(group, exp.Group):
        for expression in group.expressions:
            if isinstance(expression, exp.Column) and expression.name:
                normalized = expression.name.lower()
                if normalized not in dimension_candidates:
                    dimension_candidates.append(normalized)

    if isinstance(parsed, exp.Select):
        for projection in parsed.expressions:
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if isinstance(expression, exp.Column) and expression.name:
                normalized = expression.name.lower()
                if (
                    (_looks_like_time_column(normalized) or not _looks_like_metric_name(normalized))
                    and normalized not in dimension_candidates
                ):
                    dimension_candidates.append(normalized)

    return dimension_candidates


def _extract_time_range_from_sql(parsed: exp.Expression) -> str | None:
    between_ranges: list[str] = []
    lower_bounds: list[str] = []
    upper_bounds: list[str] = []
    exact_values: list[str] = []

    for between in parsed.find_all(exp.Between):
        if not _is_time_expression(between.this):
            continue
        low = _literal_text(between.args.get("low"))
        high = _literal_text(between.args.get("high"))
        if low and high:
            between_ranges.append(f"{low}..{high}")

    for comparator, bucket in (
        (exp.GTE, lower_bounds),
        (exp.GT, lower_bounds),
        (exp.LTE, upper_bounds),
        (exp.LT, upper_bounds),
    ):
        for node in parsed.find_all(comparator):
            if not _is_time_expression(node.this):
                continue
            value = _literal_text(node.expression)
            if value:
                bucket.append(value)

    for node in parsed.find_all(exp.EQ):
        if not _is_time_expression(node.this):
            continue
        value = _literal_text(node.expression)
        if value:
            exact_values.append(value)

    if between_ranges:
        return between_ranges[0]
    if lower_bounds and upper_bounds:
        return f"{lower_bounds[0]}..{upper_bounds[0]}"
    if exact_values:
        return exact_values[0]

    for where_clause in parsed.find_all(exp.Where):
        condition_text = where_clause.sql(dialect=None)
        range_candidate = _extract_time_range(condition_text)
        if range_candidate:
            return range_candidate
    return None


def _extract_entity_scope_from_sql(parsed: exp.Expression) -> list[str]:
    scope: list[str] = []

    for node in parsed.find_all(exp.EQ):
        if not isinstance(node.this, exp.Column) or _is_time_expression(node.this):
            continue
        column_name = node.this.name
        value = _literal_text(node.expression)
        if column_name and value:
            scope.append(f"{column_name.lower()}={value}")

    for node in parsed.find_all(exp.In):
        if not isinstance(node.this, exp.Column) or _is_time_expression(node.this):
            continue
        values = [
            value
            for value in (_literal_text(item) for item in node.expressions)
            if value
        ]
        if node.this.name and values:
            scope.append(f"{node.this.name.lower()} in ({', '.join(values)})")

    return _merge_unique([], scope)


def _is_followup_refinement(
    *,
    normalized: str,
    state: SessionState,
    extracted_dimensions: list[str],
    extracted_entity_scope: list[str],
    time_range: str | None,
) -> bool:
    has_context_to_refine = bool(state.active_metrics)
    if not has_context_to_refine:
        return False
    if extracted_dimensions or extracted_entity_scope or time_range:
        return True
    return any(hint in normalized for hint in _FOLLOWUP_HINTS)


def _looks_like_metric_name(value: str) -> bool:
    normalized = value.lower()
    return any(token in normalized for token in _METRIC_KEYWORDS)


def _looks_like_time_column(value: str) -> bool:
    normalized = value.lower()
    return any(token in normalized for token in _TIME_COLUMN_TOKENS)


def _is_time_expression(expression: exp.Expression | None) -> bool:
    return isinstance(expression, exp.Column) and _looks_like_time_column(expression.name)


def _literal_text(expression: exp.Expression | None) -> str | None:
    if isinstance(expression, exp.Literal):
        return str(expression.this).strip("'\"")
    return None


def _normalize_phrase(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip(" \t\n\r,;:.!?()[]{}\"'")
    return collapsed


def _split_phrase_list(value: str) -> list[str]:
    parts = re.split(r",|/|\b(?:и|and)\b", value, flags=re.IGNORECASE)
    return [
        normalized
        for part in parts
        if (normalized := _normalize_phrase(part))
    ]


def _split_digest_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _merge_unique(current: list[str], incoming: list[str]) -> list[str]:
    merged = list(current)
    for item in incoming:
        if item not in merged:
            merged.append(item)
    return merged
