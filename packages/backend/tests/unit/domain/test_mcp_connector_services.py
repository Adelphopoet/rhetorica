from __future__ import annotations

from pathlib import Path
from typing import Any

from rhetorica_backend.contracts.admin_integrations import (
    UpsertLlmConnectorRequest,
    UpsertMcpConnectorRequest,
)
from rhetorica_backend.domain.mcp_connectors.services import (
    McpToolCatalogService,
    McpToolCatalogSyncService,
    McpToolExecutionService,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
    ResolvedMcpConnector,
)
from rhetorica_backend.infra.postgres.models import McpConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets.stores import InMemorySecretStore


def _repository(tmp_path: Path) -> McpConnectorRepository:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/mcp_services.db")
    runtime.create_schema()
    return McpConnectorRepository(db=runtime, secret_store=InMemorySecretStore())


def _request(
    name: str,
    *,
    active: bool = True,
    allowed_tool_names: list[str] | None = None,
    visible_to_external_mcp: bool = False,
) -> UpsertMcpConnectorRequest:
    return UpsertMcpConnectorRequest.model_validate({
        "name": name,
        "workspace_id": "default",
        "is_active": active,
        "visible_to_external_mcp": visible_to_external_mcp,
        "connection": {
            "provider": "gitlab",
            "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "verify_tls": False,
            "headers": {"Enabled-Groups": "core"},
            "secret_headers": [{"name": "Authorization", "value": "token", "mode": "bearer"}],
            "enabled_groups": "core",
            "allowed_tool_names": allowed_tool_names or [],
            "timeout_seconds": 5,
            "tool_name_prefix": "mcp_gitlab",
            "llm_connector_name": "example-llm",
        },
    })


class _FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[ResolvedMcpConnector, str, dict[str, Any]]] = []

    def call_tool(
        self,
        connector: ResolvedMcpConnector,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((connector, tool_name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


class _FailingMcpClient:
    def call_tool(
        self,
        _connector: ResolvedMcpConnector,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        _ = tool_name, arguments
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("File not found: models/marts/dm/art_price_agg_app_date.sql")],
        )


class _RemoteErrorMcpClient:
    def call_tool(
        self,
        _connector: ResolvedMcpConnector,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        _ = tool_name, arguments
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Confluence client (fetcher) not available.\nTry later.",
                }
            ],
            "isError": True,
        }


class _EmptyCatalogMcpClient:
    def list_tools(self, _connector: ResolvedMcpConnector) -> list[dict[str, Any]]:
        return []


def test_mcp_sync_preserves_active_catalog_when_remote_returns_empty(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-jira"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[{"name": "jira_search", "input_schema": {"type": "object"}}],
        status="success",
    )
    service = McpToolCatalogSyncService(
        repository=repository,
        client=_EmptyCatalogMcpClient(),
    )

    count, error = service.sync_connector(
        connector_id=connector.id,
        workspace_id="default",
    )

    assert count == 0
    assert error == "MCP tools/list returned an empty catalog; preserved 1 previously active tools."
    assert [
        item.remote_tool_name for item in repository.list_active_tools(workspace_id="default")
    ] == ["jira_search"]
    stored = repository.get_by_id(connector.id, "default")
    assert stored is not None
    assert stored.last_sync_status == "failed"
    assert stored.last_sync_error == error


def test_mcp_execution_exposes_only_active_synced_tools(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-gitlab"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {
                "name": "search",
                "description": "Search code.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ],
        status="success",
    )

    service = McpToolExecutionService(repository=repository, client=_FakeMcpClient())

    definitions = service.tool_definitions(workspace_id="default")
    assert definitions[0]["function"]["name"] == "mcp_gitlab__search"
    assert service.is_tool_available(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
    )
    assert (
        repository.get_runtime_connector(
            connector_id=connector.id,
            workspace_id="default",
        ).headers["Authorization"]
        == "Bearer token"
    )
    result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
        arguments={"query": "README"},
    )
    assert result["ok"] is True
    assert result["connector_name"] == "mcp-gitlab"

    disabled = repository.update(
        connector_id=connector.id,
        workspace_id="default",
        request=_request("mcp-gitlab", active=False),
    )
    assert disabled is not None
    assert service.tool_definitions(workspace_id="default") == []
    assert not service.is_tool_available(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
    )
    disabled_result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
        arguments={},
    )
    assert disabled_result["error_code"] == "mcp_connector_disabled"


def test_mcp_catalog_rejects_page_larger_than_model_facing_bound(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(_request("mcp-gitlab"))

    result = McpToolCatalogService(repository=repository).list_tools(
        workspace_id="default",
        connector_name="mcp-gitlab",
        allowed_connector_names=None,
        limit=51,
    )

    assert result["ok"] is False
    assert result["error_code"] == "mcp_catalog_pagination_invalid"


def test_mcp_tool_allowlist_filters_definitions_and_blocks_direct_calls(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(
        _request("mcp-jira", allowed_tool_names=["search_issues", "comment_issue"])
    )
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {"name": "search_issues", "input_schema": {"type": "object"}},
            {"name": "comment_issue", "input_schema": {"type": "object"}},
            {"name": "delete_issue", "input_schema": {"type": "object"}},
        ],
        status="success",
    )
    client = _FakeMcpClient()
    service = McpToolExecutionService(repository=repository, client=client)

    definitions = service.tool_definitions(workspace_id="default")

    assert [item["function"]["name"] for item in definitions] == [
        "mcp_gitlab__comment_issue",
        "mcp_gitlab__search_issues",
    ]
    assert service.is_tool_available(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search_issues",
    )
    assert not service.is_tool_available(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__delete_issue",
    )
    blocked = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__delete_issue",
        arguments={"issue_key": "RHET-1"},
    )
    assert blocked["error_code"] == "mcp_tool_disabled"
    assert client.calls == []
    assert repository.get_by_id(
        connector.id,
        "default",
    ).connection.allowed_tool_names == ["search_issues", "comment_issue"]


def test_empty_mcp_tool_allowlist_keeps_all_synced_tools_available(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-jira", allowed_tool_names=[]))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {"name": "search_issues", "input_schema": {"type": "object"}},
            {"name": "delete_issue", "input_schema": {"type": "object"}},
        ],
        status="success",
    )

    definitions = McpToolExecutionService(repository=repository).tool_definitions(
        workspace_id="default"
    )

    assert {item["function"]["name"] for item in definitions} == {
        "mcp_gitlab__search_issues",
        "mcp_gitlab__delete_issue",
    }


def test_external_visibility_does_not_disable_internal_mcp_tools(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(
        _request("mcp-private", visible_to_external_mcp=False)
    )
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[{"name": "search", "input_schema": {"type": "object"}}],
        status="success",
    )
    client = _FakeMcpClient()
    service = McpToolExecutionService(repository=repository, client=client)

    assert [
        item["function"]["name"] for item in service.tool_definitions(workspace_id="default")
    ] == ["mcp_gitlab__search"]
    assert service.external_tool_definitions(workspace_id="default") == []
    assert not service.is_external_tool_available(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
    )

    blocked = service.execute_external(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
        arguments={},
    )
    assert blocked["error_code"] == "mcp_tool_disabled"
    assert client.calls == []

    internal = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
        arguments={},
    )
    assert internal["ok"] is True
    assert len(client.calls) == 1


def test_mcp_catalog_paginates_after_connector_tool_allowlist(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(
        _request("mcp-jira", allowed_tool_names=["comment_issue", "search_issues"])
    )
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {"name": "delete_issue", "input_schema": {"type": "object"}},
            {"name": "search_issues", "description": "Search.", "input_schema": {"type": "object"}},
            {
                "name": "comment_issue",
                "description": "Comment.",
                "input_schema": {"type": "object"},
            },
        ],
        status="success",
    )
    service = McpToolCatalogService(repository=repository)

    first_page = service.list_tools(
        workspace_id="default",
        connector_name="mcp-jira",
        allowed_connector_names=None,
        offset=0,
        limit=1,
    )
    second_page = service.list_tools(
        workspace_id="default",
        connector_name="mcp-jira",
        allowed_connector_names=None,
        offset=first_page["next_offset"],
        limit=1,
    )

    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert first_page["tools"] == [
        {
            "remote_tool_name": "comment_issue",
            "exposed_tool_name": "mcp_gitlab__comment_issue",
            "description": "Comment.",
        }
    ]
    assert second_page["tools"][0]["remote_tool_name"] == "search_issues"
    assert second_page["has_more"] is False
    assert "input_schema" not in first_page["tools"][0]


def test_mcp_catalog_rejects_inactive_and_agent_forbidden_connectors(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(_request("mcp-jira-disabled", active=False))
    service = McpToolCatalogService(repository=repository)

    inactive = service.list_tools(
        workspace_id="default",
        connector_name="mcp-jira-disabled",
        allowed_connector_names=None,
    )
    forbidden = service.list_tools(
        workspace_id="default",
        connector_name="mcp-jira-disabled",
        allowed_connector_names=["mcp-jira-main"],
    )

    assert inactive["error_code"] == "mcp_connector_unavailable"
    assert forbidden["error_code"] == "mcp_connector_not_allowed"


def test_mcp_catalog_load_is_exact_and_atomic(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-jira"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {
                "name": "search_issues",
                "description": "Search issues.",
                "input_schema": {
                    "type": "object",
                    "properties": {"jql": {"type": "string"}},
                    "required": ["jql"],
                },
            },
            {"name": "comment_issue", "input_schema": {"type": "object"}},
        ],
        status="success",
    )
    service = McpToolCatalogService(repository=repository)

    rejected = service.load_tools(
        workspace_id="default",
        connector_name="mcp-jira",
        remote_tool_names=["search_issues", "search_issue"],
        allowed_connector_names=None,
    )
    loaded = service.load_tools(
        workspace_id="default",
        connector_name="mcp-jira",
        remote_tool_names=["search_issues"],
        allowed_connector_names=None,
    )

    assert rejected.payload["error_code"] == "mcp_tools_not_found"
    assert rejected.payload["missing_remote_tool_names"] == ["search_issue"]
    assert rejected.definitions == []
    assert loaded.payload["loaded_tools"] == [
        {
            "remote_tool_name": "search_issues",
            "exposed_tool_name": "mcp_gitlab__search_issues",
        }
    ]
    assert loaded.definitions[0]["function"]["parameters"]["required"] == ["jql"]


def test_same_remote_tool_on_two_mcp_instances_stays_connector_scoped(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.upsert(_request("jira-retail"))
    second = repository.upsert(_request("jira-logistics"))
    for connector in (first, second):
        repository.replace_tool_catalog(
            connector_id=connector.id,
            workspace_id="default",
            tools=[{"name": "search_issues", "input_schema": {"type": "object"}}],
            status="success",
        )
    service = McpToolCatalogService(repository=repository)

    retail = service.list_tools(
        workspace_id="default",
        connector_name="jira-retail",
        allowed_connector_names=None,
    )
    logistics = service.list_tools(
        workspace_id="default",
        connector_name="jira-logistics",
        allowed_connector_names=None,
    )
    loaded = service.load_tools(
        workspace_id="default",
        connector_name="jira-retail",
        remote_tool_names=["search_issues"],
        allowed_connector_names=None,
    )

    assert retail["tools"][0]["remote_tool_name"] == "search_issues"
    assert logistics["tools"][0]["remote_tool_name"] == "search_issues"
    assert retail["tools"][0]["exposed_tool_name"] != logistics["tools"][0]["exposed_tool_name"]
    assert loaded.payload["connector_name"] == "jira-retail"
    assert loaded.definitions[0]["function"]["name"] == retail["tools"][0]["exposed_tool_name"]


def test_mcp_execution_returns_remote_error_message(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-gitlab"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[{"name": "get_file_contents", "input_schema": {"type": "object"}}],
        status="success",
    )
    service = McpToolExecutionService(repository=repository, client=_FailingMcpClient())

    result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__get_file_contents",
        arguments={"file_path": "missing.sql"},
    )

    assert result["ok"] is False
    assert result["error_code"] == "mcp_tool_call_failed"
    assert result["error_message"] == ("File not found: models/marts/dm/art_price_agg_app_date.sql")
    assert result["user_message"] == (
        "MCP tool call failed: File not found: models/marts/dm/art_price_agg_app_date.sql"
    )
    assert result["connector_name"] == "mcp-gitlab"
    assert result["remote_tool_name"] == "get_file_contents"


def test_mcp_execution_exposes_protocol_error_as_failed_tool_result(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-confluence"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[{"name": "search", "input_schema": {"type": "object"}}],
        status="success",
    )
    service = McpToolExecutionService(repository=repository, client=_RemoteErrorMcpClient())

    result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__search",
        arguments={"query": "товар в пути"},
    )

    assert result["ok"] is False
    assert result["error_code"] == "mcp_tool_remote_error"
    assert result["error_message"] == (
        "Confluence client (fetcher) not available. Try later."
    )
    assert result["user_message"] == (
        "MCP tool returned an error: Confluence client (fetcher) not available. Try later."
    )
    assert result["connector_name"] == "mcp-confluence"
    assert result["remote_tool_name"] == "search"
    assert result["result"]["isError"] is True


def test_mcp_execution_rejects_arguments_outside_synced_schema(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-gitlab"))
    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {
                "name": "list_commits",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["project_id"],
                    "additionalProperties": False,
                },
            }
        ],
        status="success",
    )
    client = _FakeMcpClient()
    service = McpToolExecutionService(repository=repository, client=client)

    result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__list_commits",
        arguments={
            "project_id": "example-org/analytics/dags",
            "search": "dm_operations",
        },
    )

    assert result["ok"] is False
    assert result["error_code"] == "mcp_tool_arguments_invalid"
    assert "Additional properties are not allowed" in result["validation_error"]
    assert result["connector_name"] == "mcp-gitlab"
    assert result["remote_tool_name"] == "list_commits"
    assert client.calls == []


def test_mcp_execution_unknown_prefixed_tool_does_not_fallback(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = McpToolExecutionService(repository=repository, client=_FakeMcpClient())

    result = service.execute(
        workspace_id="default",
        exposed_tool_name="mcp_gitlab__missing",
        arguments={},
    )

    assert result["ok"] is False
    assert result["error_code"] == "mcp_tool_disabled"


def test_mcp_runtime_headers_use_llm_connector_api_key_rotation(tmp_path: Path) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/mcp_llm_rotation.db")
    runtime.create_schema()
    secret_store = InMemorySecretStore()
    llm_repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
    mcp_repository = McpConnectorRepository(
        db=runtime,
        secret_store=secret_store,
        llm_connector_repository=llm_repository,
    )
    llm_repository.upsert(
        UpsertLlmConnectorRequest.model_validate({
            "name": "example-llm",
            "workspace_id": "default",
            "is_active": True,
            "connection": {
                "base_url": "https://llm.example.com/v1",
                "model": "ExampleReasoningModel",
                "api_keys": ["sk-one", "sk-two"],
            },
        })
    )
    connector = mcp_repository.upsert(_request("mcp-gitlab"))

    headers_seen = {
        mcp_repository.get_runtime_connector(
            connector_id=connector.id,
            workspace_id="default",
        ).headers["apiKey"]
        for _ in range(10)
    }

    assert headers_seen <= {"sk-one", "sk-two"}
    assert headers_seen
    assert mcp_repository.get_by_id(connector.id, "default").connection.headers == {
        "Enabled-Groups": "core"
    }


def test_mcp_tool_catalog_sanitizes_and_deduplicates_exposed_names(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    connector = repository.upsert(_request("mcp-gitlab"))

    repository.replace_tool_catalog(
        connector_id=connector.id,
        workspace_id="default",
        tools=[
            {"name": "Search Issues", "input_schema": {"type": "object"}},
            {"name": "search_issues", "input_schema": {"type": "object"}},
            {
                "name": "read.file-with-a-very-long-name-that-would-break-openai-tool-names",
                "input_schema": {"type": "object"},
            },
        ],
        status="success",
    )

    tool_names = [
        item.exposed_tool_name for item in repository.list_active_tools(workspace_id="default")
    ]

    assert len(tool_names) == 3
    assert len(set(tool_names)) == 3
    assert all(len(name) <= 64 for name in tool_names)
    assert all(name.replace("_", "").isalnum() for name in tool_names)


def test_mcp_repository_does_not_delete_external_seed_secret_refs(tmp_path: Path) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/mcp_external_secret_refs.db")
    runtime.create_schema()
    secret_store = InMemorySecretStore()
    repository = McpConnectorRepository(db=runtime, secret_store=secret_store)
    connector = repository.upsert(_request("mcp-gitlab"))
    external_secret_ref = "gitlab_connector/default/existing/api_token"
    secret_store.set_secret("default", external_secret_ref, "legacy-token")

    with runtime.session() as session:
        row = session.get(McpConnectorConfigOrm, connector.id)
        assert row is not None
        row.secret_header_refs = {
            "Authorization": {"secret_ref": external_secret_ref, "mode": "bearer"}
        }
        session.commit()

    assert repository.delete(connector_id=connector.id, workspace_id="default") is True

    assert secret_store.get_secret("default", external_secret_ref) == "legacy-token"
