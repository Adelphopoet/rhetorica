"""Shared orchestration session loading for chat flows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.conversation.conversation_manager import (
    ConversationManager,
)
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    WorkspaceCapabilitySurface,
    build_workspace_capability_surface,
)
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    TurnBuilder,
    WorkspaceToolingSnapshot,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    resolve_turn_policy,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter
from rhetorica_backend.infra.postgres.llm_connector_repository import (
    LlmConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.infra.llm.capability_registry import (
        LlmCapabilityProfile,
        LlmCapabilityRegistry,
    )


@dataclass(frozen=True)
class OrchestrationSession:
    """Resolved request-scoped orchestration inputs shared by chat runtimes."""

    request: ChatAskRequest
    conversation_id: str | None
    llm_connection: LlmConnectionConfig | None
    db_connectors: list[ConnectorItem]
    workspace_snapshot: WorkspaceToolingSnapshot
    capability_surface: WorkspaceCapabilitySurface
    llm_capability_profile: LlmCapabilityProfile | None = None
    agent_session: Any | None = None
    conversation_state: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    resolved_llm_connector_name: str | None = None


class OrchestrationSessionFactory:
    """Loads resolved request-scoped chat orchestration inputs."""

    def __init__(
        self,
        *,
        llm_connector_repository: LlmConnectorRepository,
        conversation_manager: ConversationManager,
        db_connector_repository: Any,
        turn_builder: TurnBuilder,
        sql_router: SqlRouter,
        capability_registry: LlmCapabilityRegistry | None = None,
        agent_session_factory: Callable[[str], Any] | None = None,
        llm_credential_binding_repository: LlmCredentialBindingRepository | None = None,
    ) -> None:
        self._llm_connector_repository = llm_connector_repository
        self._conversation_manager = conversation_manager
        self._db_connector_repository = db_connector_repository
        self._turn_builder = turn_builder
        self._sql_router = sql_router
        self._capability_registry = capability_registry
        self._agent_session_factory = agent_session_factory
        self._llm_credential_binding_repository = llm_credential_binding_repository

    def build(
        self,
        request: ChatAskRequest,
        *,
        create_conversation_without_llm: bool = True,
    ) -> OrchestrationSession:
        """Resolve all stable inputs required to run a chat turn."""
        resolved_llm_connector_name, llm_connection = self._get_llm_connection(request)
        if llm_connection is not None:
            update: dict[str, Any] = {}
            if request.agent_temperature is not None:
                update["temperature"] = request.agent_temperature
            if request.agent_max_tokens is not None:
                update["max_tokens"] = request.agent_max_tokens
            if update:
                llm_connection = llm_connection.model_copy(update=update)
        conversation_id = request.conversation_id
        if llm_connection is not None or create_conversation_without_llm:
            conversation_id = self._ensure_conversation(request)

        if llm_connection is not None and conversation_id is not None:
            llm_connection = self._bind_llm_credential(
                conversation_id=conversation_id,
                connection=llm_connection,
            )

        db_connectors = self._db_connector_repository.list(
            workspace_id=request.workspace_id,
            limit=500,
        )
        db_connectors = [connector for connector in db_connectors if connector.is_active]
        db_connectors = self._filter_connectors(
            db_connectors,
            request.agent_allowed_database_connector_names,
        )
        workspace_snapshot = self._turn_builder.inspect_workspace(
            request=request,
            db_connectors=db_connectors,
        )
        capability_surface = self._build_capability_surface(
            request=request,
            workspace_snapshot=workspace_snapshot,
        )
        conversation_state: dict[str, Any] = {}
        warnings: list[str] = []
        llm_capability_profile = None
        agent_session = None

        if llm_connection is not None:
            conversation_state = self._load_conversation_state(
                conversation_id=conversation_id,
            )
            if conversation_id is not None and self._agent_session_factory is not None:
                agent_session = self._agent_session_factory(conversation_id)
            # Resolve LLM capability profile
            llm_capability_profile = self._resolve_llm_capability_profile(llm_connection)

        return OrchestrationSession(
            request=request,
            conversation_id=conversation_id,
            llm_connection=llm_connection,
            db_connectors=db_connectors,
            agent_session=agent_session,
            conversation_state=conversation_state,
            warnings=warnings,
            workspace_snapshot=workspace_snapshot,
            capability_surface=capability_surface,
            llm_capability_profile=llm_capability_profile,
            resolved_llm_connector_name=resolved_llm_connector_name,
        )

    def _build_capability_surface(
        self,
        *,
        request: ChatAskRequest,
        workspace_snapshot: WorkspaceToolingSnapshot,
    ) -> WorkspaceCapabilitySurface:
        policy = resolve_turn_policy(
            request=request,
            has_db_connectors=workspace_snapshot.has_db_connectors,
            rag_configured=workspace_snapshot.rag_configured,
            documentation_configured=workspace_snapshot.documentation_configured,
            requires_metadata_lookup=FinalPayloadPolicy.requires_metadata_lookup(request),
        )
        return build_workspace_capability_surface(
            has_db_connectors=workspace_snapshot.has_db_connectors,
            rag_configured=workspace_snapshot.rag_configured,
            documentation_configured=workspace_snapshot.documentation_configured,
            policy=policy,
            run_sql_requested=request.run_sql,
        )

    def _ensure_conversation(self, request: ChatAskRequest) -> str | None:
        conversation_id = request.conversation_id
        if conversation_id is not None:
            return conversation_id

        conversation = self._conversation_manager.create_conversation(
            workspace_id=request.workspace_id,
            title=request.question[:100] if len(request.question) > 100 else request.question,
            agent_id=request.agent_id,
        )
        return conversation.id if conversation else None

    def _load_conversation_state(
        self,
        *,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        if not conversation_id:
            return {}
        get_state = getattr(self._conversation_manager, "get_conversation_state", None)
        if not callable(get_state):
            return {}
        return get_state(
            conversation_id=conversation_id,
        )

    def _bind_llm_credential(
        self,
        *,
        conversation_id: str,
        connection: LlmConnectionConfig,
    ) -> LlmConnectionConfig | None:
        credentials = list(connection.api_keys or [])
        if not credentials and connection.api_key:
            credentials = [connection.api_key]
        if not credentials:
            return None

        repository = self._llm_credential_binding_repository
        connector_id = getattr(connection, "connector_id", None)
        if repository is None or connector_id is None:
            return connection
        selected_index = repository.resolve(
            conversation_id=conversation_id,
            llm_connector_id=connector_id,
            credential_set_version=connection.credential_set_version,
            credential_count=len(credentials),
        )

        if connection.api_keys != credentials:
            connection = connection.model_copy(update={"api_keys": credentials})
            connection.configure_connector_identity(
                connector_id=connector_id,
                credential_set_version=connection.credential_set_version,
            )

        connection.api_key = credentials[selected_index]
        connection.configure_credential_binding(
            connector_id=connector_id,
            credential_set_version=connection.credential_set_version,
            selected_api_key_index=selected_index,
            rebind=lambda new_index, expected_index: repository.rebind(
                conversation_id=conversation_id,
                llm_connector_id=connector_id,
                expected_index=expected_index,
                credential_set_version=connection.credential_set_version,
                new_index=new_index,
            ),
        )
        return connection

    def _get_llm_connection(
        self,
        request: ChatAskRequest,
    ) -> tuple[str | None, LlmConnectionConfig | None]:
        llm_connector_name = request.llm_connector_name
        if not llm_connector_name:
            connectors = self._llm_connector_repository.list(
                workspace_id=request.workspace_id,
                limit=100,
            )
            connectors = [connector for connector in connectors if connector.is_active]
            if not connectors:
                return None, None
            llm_connector_name = connectors[0].name

        return llm_connector_name, self._llm_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=llm_connector_name,
        )

    @staticmethod
    def _filter_connectors(
        connectors: list[ConnectorItem],
        allowed_names: list[str] | None,
    ) -> list[ConnectorItem]:
        if allowed_names is None:
            return connectors
        allowed = {name for name in allowed_names if name}
        if not allowed:
            return []
        return [connector for connector in connectors if connector.name in allowed]

    def _resolve_llm_capability_profile(
        self,
        connection: LlmConnectionConfig,
    ) -> LlmCapabilityProfile | None:
        """Resolve LLM capability profile from connection config."""
        if self._capability_registry is None:
            return None
        return self._capability_registry.resolve_for_connector(connection)
