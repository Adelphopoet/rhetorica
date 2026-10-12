from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.turn_builder import TurnBuilder


class _SqlRouterStub:
    pass


class _McpConnectorRepositoryStub:
    def list(self, **_kwargs):
        return [
            SimpleNamespace(
                name="catalog-mcp",
                is_active=True,
                connection=SimpleNamespace(
                    provider="streamable_http",
                    tool_name_prefix="catalog",
                    enabled_groups="discovery",
                ),
                usage_description="Use for governed discovery",
            ),
            SimpleNamespace(
                name="ops-mcp",
                is_active=True,
                connection=SimpleNamespace(
                    provider="streamable_http",
                    tool_name_prefix="ops",
                    enabled_groups="operations",
                ),
                usage_description="Use for operations",
            ),
        ]

    def list_active_tools(self, **_kwargs):
        return []


def _builder() -> TurnBuilder:
    return TurnBuilder(
        sql_router=_SqlRouterStub(),
        mcp_connector_repository=_McpConnectorRepositoryStub(),
    )


def _tool_names(prepared) -> set[str]:
    return {
        definition["function"]["name"]
        for definition in prepared.tools
        if definition.get("type") == "function"
    }


def test_optimized_prompt_reports_the_version_actually_sent(monkeypatch):
    from rhetorica_backend.domain.chat import settings
    from rhetorica_backend.domain.chat.llm.prompt_contract import PROMPT_VERSION

    monkeypatch.setattr(settings, "_settings", settings.ChatSettings(context_harness_enabled=True))
    prepared = _builder().prepare_turn(request=ChatAskRequest(question="test"), db_connectors=[])
    assert prepared.state.prompt_version == PROMPT_VERSION
    assert f"prompt_version={prepared.state.prompt_version}" in prepared.messages[0]["content"]


def test_prepare_turn_exposes_only_generic_mcp_discovery_initially() -> None:
    prepared = _builder().prepare_turn(
        request=ChatAskRequest(
            question="find the governed metric",
            workspace_id="default",
            run_sql=False,
        ),
        db_connectors=[],
    )

    assert "name=catalog-mcp" in prepared.messages[-1]["content"]
    assert _tool_names(prepared) == {
        "load_tool_group",
        "unload_tool_group",
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
        "list_recent_tool_artifacts",
    }
    assert prepared.state.available_tool_groups["mcp_connectors"] == (
        "list_mcp_tools",
        "load_mcp_tools",
        "unload_mcp_tools",
    )


def test_prepare_turn_filters_mcp_connectors_by_agent_allowlist() -> None:
    prepared = _builder().prepare_turn(
        request=ChatAskRequest(
            question="inspect operations",
            workspace_id="default",
            run_sql=False,
            agent_id="agent-1",
            agent_allowed_mcp_connector_names=["ops-mcp"],
        ),
        db_connectors=[],
    )

    content = prepared.messages[-1]["content"]
    assert "ops-mcp" in content
    assert "catalog-mcp" not in content


def test_prepare_turn_blocks_mcp_for_empty_agent_allowlist() -> None:
    prepared = _builder().prepare_turn(
        request=ChatAskRequest(
            question="hello",
            workspace_id="default",
            run_sql=False,
            agent_id="agent-1",
            agent_allowed_mcp_connector_names=[],
        ),
        db_connectors=[],
    )

    assert "mcp_tools_enabled" not in prepared.state.capability_state.policy_decisions
    assert "MCP connectors" not in prepared.messages[-1]["content"]
