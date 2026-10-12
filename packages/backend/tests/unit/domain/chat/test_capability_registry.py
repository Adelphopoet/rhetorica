from __future__ import annotations

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    build_workspace_capability_surface,
    resolve_available_tool_names,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import resolve_turn_policy


def test_capability_surface_matches_active_native_catalog() -> None:
    request = ChatAskRequest(question="analyze", workspace_id="default", run_sql=True)
    policy = resolve_turn_policy(
        request=request,
        has_db_connectors=True,
        rag_configured=True,
        documentation_configured=True,
        mcp_configured=True,
        requires_metadata_lookup=False,
    )

    assert resolve_available_tool_names(policy) == [
        "run_sql",
        "build_visualization",
        "search_rag_insights",
        "search_documentation",
        "read_documentation_page",
    ]
    surface = build_workspace_capability_surface(
        has_db_connectors=True,
        rag_configured=True,
        documentation_configured=True,
        mcp_configured=True,
        policy=policy,
        run_sql_requested=True,
    )
    assert surface.available_tools == resolve_available_tool_names(policy)
    assert "mcp_tools_enabled" in surface.policy_decisions
