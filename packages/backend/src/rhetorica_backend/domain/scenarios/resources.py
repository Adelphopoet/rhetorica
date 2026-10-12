"""Current actor permissions intersected with run-time agent snapshots."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.agents import AgentProfileItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.scenario_workflows import LlmNode, ScenarioWorkflowDefinition
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem, UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository

ANALYTICAL_TOOLS = {
    "run_sql": "Выполнить защищённый SQL и сохранить результат в файл",
    "run_python": "Обработать входные файлы в Python sandbox",
    "list_files": "Показать файлы текущего запуска",
    "list_database_connectors": "Показать разрешённые коннекторы данных",
}


class ScenarioResourcePolicy:
    def __init__(
        self,
        users: UserAccountRepository,
        agents: AgentProfileRepository,
        connectors: ConnectorRepository,
        llm_connectors: LlmConnectorRepository,
        workspaces: WorkspaceRepository | None = None,
    ):
        self.users = users
        self.agents = agents
        self.connectors = connectors
        self.llm_connectors = llm_connectors
        self.workspaces = workspaces

    def user(self, user_id: str, workspace_id: str) -> UserAccountItem:
        user = self.users.get_by_id(user_id=user_id)
        if user is None or not user.is_active:
            raise WorkflowError("scenario_actor_access_revoked", status_code=403)
        if self.workspaces is None:
            if user.workspace_id != workspace_id:
                raise WorkflowError("scenario_actor_access_revoked", status_code=403)
        elif self.workspaces.membership(workspace_id=workspace_id, user_id=user.id) is None:
            raise WorkflowError("scenario_actor_access_revoked", status_code=403)
        return user.for_workspace(workspace_id)

    def agent(self, agent_id: str, user: UserAccountItem) -> AgentProfileItem:
        agent = self.agents.get(
            agent_id=agent_id,
            workspace_id=user.workspace_id,
            current_user_id=user.id,
            active_only=True,
        )
        if agent is None:
            raise WorkflowError("scenario_agent_unavailable", status_code=403)
        return agent

    def snapshots(
        self,
        definition: ScenarioWorkflowDefinition,
        user: UserAccountItem,
        *,
        legacy: dict[str, Any] | None = None,
        authoring: bool = False,
    ) -> dict[str, Any]:
        snapshots: dict[str, Any] = {}
        for node in definition.nodes:
            if node.type == "sql":
                self.database(node.connector_name, user)
            if not isinstance(node, LlmNode):
                continue
            if legacy is not None:
                bindings = legacy.get("default_connector_bindings") or {}
                name = bindings.get("llm_connector_name")
                agent_dump = None
            else:
                agent = self.agent(node.agent_id, user)
                name = agent.llm_connector_name
                agent_dump = agent.model_dump(mode="json")
            name, connection = self.llm_connection(user, name)
            if (
                not authoring
                and node.output_format == "json"
                and not LlmClient._structured_output_enabled(connection)
            ):
                raise WorkflowError("structured_output_not_supported")
            if set(node.allowed_tools) - set(ANALYTICAL_TOOLS):
                raise WorkflowError("non_analytical_tool_forbidden")
            snapshots[node.id] = {
                **(
                    {
                        "legacy_database_connector_name": bindings.get("database_connector_name"),
                        "legacy_run_sql_allowed": (legacy.get("tool_policy") or {}).get(
                            "allow_run_sql", True
                        )
                        and bindings.get("run_sql") is not False,
                    }
                    if legacy is not None
                    else {}
                ),
                "agent": agent_dump,
                "llm_connector_name": name,
                "llm_config": connection.model_dump(
                    mode="json",
                    exclude={"api_key", "api_keys", "retained_api_key_indexes", "headers"},
                ),
            }
        return snapshots

    def llm_connection(
        self, user: UserAccountItem, name: str | None
    ) -> tuple[str, LlmConnectionConfig]:
        if not name:
            default = self.llm_connectors.get(user.workspace_id)
            if default is None:
                raise WorkflowError("default_llm_connector_required")
            name = default.name
        connection = self.llm_connectors.get_connection(user.workspace_id, name)
        if connection is None:
            raise WorkflowError("llm_connector_unavailable")
        return name, connection

    def database(
        self, name: str, user: UserAccountItem, *, snapshot: dict[str, Any] | None = None
    ) -> EngineConnectionConfig:
        if not name:
            raise WorkflowError("database_connector_required")
        if (
            snapshot
            and "legacy_run_sql_allowed" in snapshot
            and (
                not snapshot["legacy_run_sql_allowed"]
                or (
                    snapshot.get("legacy_database_connector_name")
                    and name != snapshot["legacy_database_connector_name"]
                )
            )
        ):
            raise WorkflowError("legacy_database_access_denied", status_code=403)
        if snapshot and snapshot.get("agent"):
            saved = AgentProfileItem.model_validate(snapshot["agent"])
            current = self.agent(saved.id, user)
            if (
                name not in saved.allowed_database_connector_names
                or name not in current.allowed_database_connector_names
                or not saved.settings.run_sql_allowed
                or not current.settings.run_sql_allowed
            ):
                raise WorkflowError("agent_database_access_denied", status_code=403)
        connection = self.connectors.get_connection_for_execution(
            workspace_id=user.workspace_id, name=name
        )
        if connection is None:
            raise WorkflowError("database_connector_unavailable", status_code=403)
        return connection

    def capabilities(self, user: UserAccountItem) -> dict[str, Any]:
        agents = self.agents.list(
            workspace_id=user.workspace_id,
            current_user_id=user.id,
            include_inactive=False,
            limit=100,
        )
        result = []
        for agent in agents:
            supported = False
            try:
                _, connection = self.llm_connection(user, agent.llm_connector_name)
                supported = LlmClient._structured_output_enabled(connection)
            except WorkflowError:
                pass
            result.append({
                "id": agent.id,
                "title": agent.title,
                "structured_output": supported,
                "database_connectors": agent.allowed_database_connector_names,
                "run_sql_allowed": agent.settings.run_sql_allowed,
            })
        return {
            "agents": result,
            "database_connectors": [
                {"name": item.name, "engine": item.connection.engine}
                for item in self.connectors.list(user.workspace_id, limit=100)
                if item.is_active
            ],
            "tools": [
                {"name": name, "description": description}
                for name, description in ANALYTICAL_TOOLS.items()
            ],
        }
