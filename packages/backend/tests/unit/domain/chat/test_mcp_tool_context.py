from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import UpsertMcpConnectorRequest
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.shared_kernel import ensure_dynamic_tools
from rhetorica_backend.domain.chat.orchestration.turn_builder import TurnBuilder
from rhetorica_backend.infra.postgres.mcp_connector_repository import McpConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets.stores import InMemorySecretStore


class _SqlRouterStub:
    pass


def _repository(tmp_path: Path) -> McpConnectorRepository:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/mcp_tool_context.db")
    runtime.create_schema()
    return McpConnectorRepository(db=runtime, secret_store=InMemorySecretStore())


def _connector_request(
    *,
    name: str,
    prefix: str,
    usage_description: str,
) -> UpsertMcpConnectorRequest:
    return UpsertMcpConnectorRequest.model_validate({
        "name": name,
        "workspace_id": "default",
        "is_active": True,
        "connection": {
            "provider": "jira",
            "url": f"https://{name}.example.com/mcp",
            "tool_name_prefix": prefix,
            "usage_description": usage_description,
        },
    })


def test_model_controls_exact_mcp_instance_and_dynamic_schema_loading(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    retail = repository.upsert(
        _connector_request(
            name="jira-retail",
            prefix="jira_retail",
            usage_description="Retail Jira instance.",
        )
    )
    logistics = repository.upsert(
        _connector_request(
            name="jira-logistics",
            prefix="jira_logistics",
            usage_description="Logistics Jira instance.",
        )
    )
    for connector in (retail, logistics):
        repository.replace_tool_catalog(
            connector_id=connector.id,
            workspace_id="default",
            tools=[
                {
                    "name": "search_issues",
                    "description": "Search issues in this Jira instance.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"jql": {"type": "string"}},
                        "required": ["jql"],
                    },
                }
            ],
            status="success",
        )

    request = ChatAskRequest(
        question="Найди задачу в retail Jira",
        workspace_id="default",
        run_sql=False,
        agent_allowed_mcp_connector_names=["jira-retail", "jira-logistics"],
    )
    prepared = TurnBuilder(
        sql_router=_SqlRouterStub(),
        mcp_connector_repository=repository,
    ).prepare_turn(
        request=request,
        db_connectors=[],
    )

    initial_names = {tool["function"]["name"] for tool in prepared.tools}
    user_context = prepared.messages[-1]["content"]
    assert "name=jira-retail" in user_context
    assert "use_when=Retail Jira instance." in user_context
    assert "name=jira-logistics" in user_context
    assert "use_when=Logistics Jira instance." in user_context
    assert initial_names == {
        "load_tool_group",
        "unload_tool_group",
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
        "list_recent_tool_artifacts",
    }
    assert "jira_retail__search_issues" not in initial_names
    assert "jira_logistics__search_issues" not in initial_names

    assert "- mcp_connectors:" in prepared.messages[0]["content"]

    group_loaded = prepared.tool_executor.execute_tool(
        tool_name="load_tool_group",
        arguments={"group_name": "mcp_connectors"},
        request=request,
        db_connectors=[],
        state=prepared.state,
    )
    assert group_loaded["loaded_tool_names"] == [
        "list_mcp_tools",
        "load_mcp_tools",
        "unload_mcp_tools",
    ]
    group_tools = ensure_dynamic_tools(
        tools=prepared.tools,
        request=request,
        db_connectors=[],
        state=prepared.state,
    )
    assert {tool["function"]["name"] for tool in group_tools} == {
        "load_tool_group",
        "unload_tool_group",
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
        "list_recent_tool_artifacts",
        "list_mcp_tools",
        "load_mcp_tools",
        "unload_mcp_tools",
    }

    catalog = prepared.tool_executor.execute_tool(
        tool_name="list_mcp_tools",
        arguments={"connector_name": "jira-retail", "offset": 0, "limit": 50},
        request=request,
        db_connectors=[],
        state=prepared.state,
    )
    assert catalog["connector_name"] == "jira-retail"
    assert catalog["tools"][0]["remote_tool_name"] == "search_issues"
    assert "input_schema" not in catalog["tools"][0]

    loaded = prepared.tool_executor.execute_tool(
        tool_name="load_mcp_tools",
        arguments={
            "connector_name": "jira-retail",
            "remote_tool_names": ["search_issues"],
        },
        request=request,
        db_connectors=[],
        state=prepared.state,
    )
    next_tools = ensure_dynamic_tools(
        tools=prepared.tools,
        request=request,
        db_connectors=[],
        state=prepared.state,
    )
    next_names = {tool["function"]["name"] for tool in next_tools}

    assert loaded == {
        "ok": True,
        "connector_name": "jira-retail",
        "loaded_tools": [
            {
                "remote_tool_name": "search_issues",
                "exposed_tool_name": "jira_retail__search_issues",
            }
        ],
    }
    assert "jira_retail__search_issues" in next_names
    assert "jira_logistics__search_issues" not in next_names
    assert "jira_retail__search_issues" in prepared.state.capability_state.available_tools

    restored = TurnBuilder(
        sql_router=_SqlRouterStub(),
        mcp_connector_repository=repository,
    ).prepare_turn(
        request=request,
        db_connectors=[],
        conversation_state={
            "tool_activation": {
                "group_names": ["mcp_connectors"],
                "mcp_tools": [
                    {
                        "connector_name": "jira-retail",
                        "remote_tool_names": ["search_issues"],
                    }
                ],
            }
        },
    )
    restored_names = {
        tool["function"]["name"]
        for tool in ensure_dynamic_tools(
            tools=restored.tools,
            request=request,
            db_connectors=[],
            state=restored.state,
        )
    }
    assert "jira_retail__search_issues" in restored_names
    assert "jira_logistics__search_issues" not in restored_names

    unloaded = restored.tool_executor.execute_tool(
        tool_name="unload_mcp_tools",
        arguments={
            "connector_name": "jira-retail",
            "remote_tool_names": ["search_issues"],
        },
        request=request,
        db_connectors=[],
        state=restored.state,
    )
    assert unloaded["unloaded_tools"][0]["exposed_tool_name"] == ("jira_retail__search_issues")
    assert restored.state.loaded_mcp_remote_tool_names == {}
