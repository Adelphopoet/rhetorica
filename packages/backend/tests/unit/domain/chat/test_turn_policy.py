from __future__ import annotations

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.turn_policy import resolve_turn_policy


def _request(*, run_sql: bool = True) -> ChatAskRequest:
    return ChatAskRequest(question="hello", workspace_id="default", run_sql=run_sql)


def test_sql_capability_selects_sql_generation_without_text_classification() -> None:
    policy = resolve_turn_policy(
        request=_request(),
        has_db_connectors=True,
        rag_configured=False,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "sql_generation"
    assert policy.run_sql_tools_enabled is True


def test_generic_mcp_selects_discovery_for_any_question() -> None:
    policy = resolve_turn_policy(
        request=_request(run_sql=False),
        has_db_connectors=False,
        rag_configured=False,
        mcp_configured=True,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "discovery"
    assert policy.mcp_tools_enabled is True


def test_no_capabilities_selects_direct_response() -> None:
    policy = resolve_turn_policy(
        request=_request(run_sql=False),
        has_db_connectors=False,
        rag_configured=False,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "direct_response"
    assert policy.tools_available is False
