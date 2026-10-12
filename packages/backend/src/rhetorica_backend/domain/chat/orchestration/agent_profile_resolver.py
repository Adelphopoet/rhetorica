from __future__ import annotations

from typing import Protocol

from rhetorica_backend.contracts.agents import AgentProfileItem
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.infra.postgres.chat_repository import ChatConversationItem


class AgentProfileResolutionError(ValueError):
    """Stable domain error raised before chat orchestration starts."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AgentProfileLookup(Protocol):
    def get(
        self,
        *,
        agent_id: str,
        workspace_id: str,
        current_user_id: str | None = None,
        active_only: bool = True,
    ) -> AgentProfileItem | None: ...


class AgentProfileResolver:
    """Resolve a conversation-pinned agent and apply its runtime policy."""

    def __init__(self, repository: AgentProfileLookup) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        request: ChatAskRequest,
        current_user_id: str | None,
        existing_conversation: ChatConversationItem | None = None,
    ) -> tuple[ChatAskRequest, AgentProfileItem | None]:
        requested_agent_id = request.agent_id
        conversation_agent_id = existing_conversation.agent_id if existing_conversation else None
        resolved_agent_id = conversation_agent_id or requested_agent_id

        if conversation_agent_id and requested_agent_id and requested_agent_id != conversation_agent_id:
            raise AgentProfileResolutionError("conversation_agent_locked")
        if existing_conversation and conversation_agent_id is None and requested_agent_id is not None:
            raise AgentProfileResolutionError("conversation_agent_locked")
        if resolved_agent_id is None:
            return request, None

        agent = self._repository.get(
            agent_id=resolved_agent_id,
            workspace_id=request.workspace_id,
            current_user_id=current_user_id,
            active_only=True,
        )
        if agent is None:
            raise AgentProfileResolutionError("agent_not_found")

        update: dict[str, object] = {
            "agent_id": agent.id,
            "agent_prompt": agent.admin_prompt,
            "agent_allowed_database_connector_names": agent.allowed_database_connector_names,
            "agent_allowed_rag_connector_names": agent.allowed_rag_connector_names,
            "agent_allowed_documentation_connector_names": (
                agent.allowed_documentation_connector_names
            ),
            "agent_allowed_gitlab_connector_names": agent.allowed_gitlab_connector_names,
            "agent_allowed_mcp_connector_names": agent.allowed_mcp_connector_names,
            "agent_allowed_airflow_connector_names": agent.allowed_airflow_connector_names,
            "agent_allowed_kubernetes_connector_names": agent.allowed_kubernetes_connector_names,
            "agent_allowed_victoriametrics_connector_names": (
                agent.allowed_victoriametrics_connector_names
            ),
            "agent_allowed_workspace_skill_ids": agent.allowed_workspace_skill_ids,
            "run_sql": request.run_sql and agent.settings.run_sql_allowed,
            "agent_temperature": agent.settings.temperature,
            "agent_max_tokens": agent.settings.max_tokens,
        }
        if agent.llm_connector_name:
            update["llm_connector_name"] = agent.llm_connector_name
        return request.model_copy(update=update), agent
