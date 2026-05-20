"""Turn-level orchestration policy for prompt and tool selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rhetorica_backend.contracts.chat import ChatAskRequest
    from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter


PromptMode = Literal["direct_response", "discovery", "sql_generation"]

_SQL_INSPECTION_COMMAND_PATTERN = re.compile(
    r"^(?:describe|desc|show\s+create(?:\s+table)?|exists|explain)\s+[`\"a-z0-9_.]+$"
)
_SQL_INSPECTION_EXECUTION_PATTERN = re.compile(
    r"\b(?:run|execute|выполни|запусти|запустить|попробуй\s+запустить)\s+"
    r"(?:sql\s+)?"
    r"(?:describe|desc|show\s+create(?:\s+table)?|exists|explain)\s+[`\"a-z0-9_.]+\b"
)
_JIRA_REFERENCE_PATTERN = re.compile(
    r"\b(?:jira|ticket|issue|тикет|задач[ауи]?|инцидент|[A-Z][A-Z0-9]+-\d+)\b",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class TurnPolicy:
    """Resolved orchestration policy for the current user turn."""

    prompt_mode: PromptMode
    metadata_tools_enabled: bool
    rag_tools_enabled: bool
    confluence_tools_enabled: bool
    run_sql_tools_enabled: bool
    profile_tools_enabled: bool
    is_non_analytic_question: bool
    requires_metadata_lookup: bool
    jira_read_tools_enabled: bool = False
    jira_write_tools_enabled: bool = False

    @property
    def tools_available(self) -> bool:
        return any(
            (
                self.metadata_tools_enabled,
                self.rag_tools_enabled,
                self.confluence_tools_enabled,
                self.jira_read_tools_enabled,
                self.jira_write_tools_enabled,
                self.run_sql_tools_enabled,
                self.profile_tools_enabled,
            )
        )


def is_explicit_sql_inspection_request(question: str) -> bool:
    """Return True for explicit SQL inspection commands embedded in user text."""
    normalized = " ".join(question.casefold().split())
    if not normalized:
        return False
    return bool(
        _SQL_INSPECTION_COMMAND_PATTERN.match(normalized)
        or _SQL_INSPECTION_EXECUTION_PATTERN.search(normalized)
    )


def references_jira_work_item(question: str) -> bool:
    """Return True when the user explicitly asks about an issue/ticket."""
    normalized = " ".join(question.split())
    if not normalized:
        return False
    return bool(_JIRA_REFERENCE_PATTERN.search(normalized))


def resolve_turn_policy(
    *,
    request: ChatAskRequest,
    has_db_connectors: bool,
    metadata_configured: bool,
    rag_configured: bool,
    confluence_configured: bool,
    sql_router: SqlRouter | None,
    requires_metadata_lookup: bool,
    jira_configured: bool = False,
) -> TurnPolicy:
    """Resolve prompt mode and tool availability for the current turn."""
    explicit_sql_inspection = is_explicit_sql_inspection_request(request.question)
    resolved_requires_metadata_lookup = (
        requires_metadata_lookup and not explicit_sql_inspection
    )
    is_non_analytic_question = (
        sql_router.is_non_analytic_question(request.question)
        if sql_router is not None
        else False
    )
    metadata_tools_enabled = metadata_configured and not is_non_analytic_question
    rag_tools_enabled = rag_configured and not is_non_analytic_question
    confluence_tools_enabled = confluence_configured and not is_non_analytic_question
    jira_referenced = references_jira_work_item(request.question)
    jira_read_tools_enabled = jira_configured and (
        jira_referenced or not is_non_analytic_question
    )
    jira_write_tools_enabled = jira_configured and (
        jira_referenced or not is_non_analytic_question
    )
    run_sql_tools_enabled = (
        request.run_sql
        and has_db_connectors
        and not is_non_analytic_question
        and (explicit_sql_inspection or not resolved_requires_metadata_lookup)
    )
    profile_tools_enabled = has_db_connectors and not is_non_analytic_question

    if run_sql_tools_enabled:
        prompt_mode: PromptMode = "sql_generation"
    elif (
        metadata_tools_enabled
        or rag_tools_enabled
        or confluence_tools_enabled
        or jira_read_tools_enabled
        or jira_write_tools_enabled
        or profile_tools_enabled
    ):
        prompt_mode = "discovery"
    else:
        prompt_mode = "direct_response"

    return TurnPolicy(
        prompt_mode=prompt_mode,
        metadata_tools_enabled=metadata_tools_enabled,
        rag_tools_enabled=rag_tools_enabled,
        confluence_tools_enabled=confluence_tools_enabled,
        jira_read_tools_enabled=jira_read_tools_enabled,
        jira_write_tools_enabled=jira_write_tools_enabled,
        run_sql_tools_enabled=run_sql_tools_enabled,
        profile_tools_enabled=profile_tools_enabled,
        is_non_analytic_question=is_non_analytic_question,
        requires_metadata_lookup=resolved_requires_metadata_lookup,
    )
