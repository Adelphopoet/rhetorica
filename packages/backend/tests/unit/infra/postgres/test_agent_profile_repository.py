from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.agents import UpsertAgentProfileRequest
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/agent_profiles.db")
    runtime.create_schema()
    return runtime


def test_agent_profile_persists_allowed_airflow_connectors(tmp_path: Path) -> None:
    repository = AgentProfileRepository(db=_runtime(tmp_path))

    created = repository.upsert(
        UpsertAgentProfileRequest(
            workspace_id="default",
            slug="airflow-agent",
            title="Airflow Agent",
            allowed_airflow_connector_names=["airflow-main", "airflow-main", " airflow-dev "],
        ),
    )

    assert created.allowed_airflow_connector_names == ["airflow-main", "airflow-dev"]

    fetched = repository.get(
        agent_id=created.id,
        workspace_id="default",
    )
    assert fetched is not None
    assert fetched.allowed_airflow_connector_names == ["airflow-main", "airflow-dev"]


def test_agent_profile_persists_allowed_mcp_connectors(tmp_path: Path) -> None:
    repository = AgentProfileRepository(db=_runtime(tmp_path))

    created = repository.upsert(
        UpsertAgentProfileRequest(
            workspace_id="default",
            slug="mcp-agent",
            title="MCP Agent",
            allowed_mcp_connector_names=["mcp-jira-omni", "mcp-jira-omni", " mcp-confluence-main "],
        ),
    )

    assert created.allowed_mcp_connector_names == ["mcp-jira-omni", "mcp-confluence-main"]

    fetched = repository.get(
        agent_id=created.id,
        workspace_id="default",
    )
    assert fetched is not None
    assert fetched.allowed_mcp_connector_names == ["mcp-jira-omni", "mcp-confluence-main"]
