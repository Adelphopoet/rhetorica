"""Turn-level orchestration policy for prompt and tool selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rhetorica_backend.contracts.chat import ChatAskRequest


PromptMode = Literal["direct_response", "discovery", "sql_generation"]

@dataclass(frozen=True)
class TurnPolicy:
    """Resolved orchestration policy for the current user turn."""

    prompt_mode: PromptMode
    rag_tools_enabled: bool
    run_sql_tools_enabled: bool
    requires_metadata_lookup: bool
    gitlab_tools_enabled: bool = False
    airflow_tools_enabled: bool = False
    kubernetes_tools_enabled: bool = False
    victoriametrics_tools_enabled: bool = False
    workspace_skill_tools_enabled: bool = False
    documentation_tools_enabled: bool = False
    mcp_tools_enabled: bool = False

    @property
    def tools_available(self) -> bool:
        return any(
            (
                self.rag_tools_enabled,
                self.documentation_tools_enabled,
                self.gitlab_tools_enabled,
                self.airflow_tools_enabled,
                self.kubernetes_tools_enabled,
                self.victoriametrics_tools_enabled,
                self.run_sql_tools_enabled,
                self.workspace_skill_tools_enabled,
                self.mcp_tools_enabled,
            )
        )


def resolve_turn_policy(
    *,
    request: ChatAskRequest,
    has_db_connectors: bool,
    rag_configured: bool,
    documentation_configured: bool = False,
    requires_metadata_lookup: bool,
    gitlab_configured: bool = False,
    airflow_configured: bool = False,
    kubernetes_configured: bool = False,
    victoriametrics_configured: bool = False,
    chat_skills_configured: bool = False,
    mcp_configured: bool = False,
) -> TurnPolicy:
    """Resolve prompt mode and tool availability for the current turn."""
    # Question classification may guide prompting, but capability availability is
    # determined only by configuration and explicit request/agent permissions.
    # The LLM must be able to choose among all capabilities that actually exist.
    rag_tools_enabled = rag_configured
    documentation_tools_enabled = documentation_configured
    gitlab_tools_enabled = gitlab_configured
    airflow_tools_enabled = airflow_configured
    kubernetes_tools_enabled = kubernetes_configured
    victoriametrics_tools_enabled = victoriametrics_configured
    mcp_tools_enabled = mcp_configured
    run_sql_tools_enabled = request.run_sql and has_db_connectors
    workspace_skill_tools_enabled = chat_skills_configured

    if run_sql_tools_enabled:
        prompt_mode: PromptMode = "sql_generation"
    elif (
        rag_tools_enabled
        or documentation_tools_enabled
        or gitlab_tools_enabled
        or airflow_tools_enabled
        or kubernetes_tools_enabled
        or victoriametrics_tools_enabled
        or workspace_skill_tools_enabled
        or mcp_tools_enabled
    ):
        prompt_mode = "discovery"
    else:
        prompt_mode = "direct_response"

    return TurnPolicy(
        prompt_mode=prompt_mode,
        rag_tools_enabled=rag_tools_enabled,
        documentation_tools_enabled=documentation_tools_enabled,
        gitlab_tools_enabled=gitlab_tools_enabled,
        airflow_tools_enabled=airflow_tools_enabled,
        kubernetes_tools_enabled=kubernetes_tools_enabled,
        victoriametrics_tools_enabled=victoriametrics_tools_enabled,
        run_sql_tools_enabled=run_sql_tools_enabled,
        workspace_skill_tools_enabled=workspace_skill_tools_enabled,
        mcp_tools_enabled=mcp_tools_enabled,
        requires_metadata_lookup=requires_metadata_lookup,
    )
