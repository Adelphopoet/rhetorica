from __future__ import annotations

from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.session import (
    OrchestrationSessionFactory,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    WorkspaceToolingSnapshot,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore


def test_session_factory_builds_agent_session_and_capability_surface() -> None:
    agent_session = object()
    factory = OrchestrationSessionFactory(
        llm_connector_repository=SimpleNamespace(
            list=lambda **_kwargs: [SimpleNamespace(name="gpt-main", is_active=True)],
            get_connection=lambda **_kwargs: LlmConnectionConfig(
                base_url="https://llm.example.test/v1",
                model="gpt-4.1",
                api_key="test-key",
                extra_settings={"context_window_tokens": 8192},
            ),
        ),
        conversation_manager=SimpleNamespace(
            create_conversation=lambda **_kwargs: SimpleNamespace(id="conv-1"),
        ),
        db_connector_repository=SimpleNamespace(
            list=lambda **_kwargs: [SimpleNamespace(name="warehouse", is_active=True)]
        ),
        turn_builder=SimpleNamespace(
            inspect_workspace=lambda **_kwargs: WorkspaceToolingSnapshot(
                has_db_connectors=True,
                rag_configured=False,
            )
        ),
        sql_router=SimpleNamespace(),
        agent_session_factory=lambda conversation_id: (
            agent_session if conversation_id == "conv-1" else None
        ),
    )

    session = factory.build(ChatAskRequest(question="Покажи выручку по дням", workspace_id="ws-1"))

    assert session.conversation_id == "conv-1"
    assert session.agent_session is agent_session
    assert session.warnings == []
    assert session.workspace_snapshot.has_db_connectors is True
    assert "run_sql" in session.capability_surface.available_tools


def _binding_factory(
    repository: LlmConnectorRepository,
    binding_repository: LlmCredentialBindingRepository,
) -> OrchestrationSessionFactory:
    return OrchestrationSessionFactory(
        llm_connector_repository=repository,
        conversation_manager=SimpleNamespace(),
        db_connector_repository=SimpleNamespace(),
        turn_builder=SimpleNamespace(),
        sql_router=SimpleNamespace(),
        llm_credential_binding_repository=binding_repository,
    )


def test_session_binding_survives_new_factory_and_successful_rebind(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'conversation_binding.db'}"
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    connector_repository = LlmConnectorRepository(
        db=runtime,
        secret_store=InMemorySecretStore(),
    )
    connector_repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.test/v1",
                model="test-model",
                api_keys=["key-a", "key-b"],
            ),
        )
    )
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_credential_binding_repository.secrets.randbelow",
        lambda _count: 0,
    )

    first_factory = _binding_factory(
        connector_repository,
        LlmCredentialBindingRepository(runtime),
    )
    first_connection = connector_repository.get_connection(workspace_id="ws-1", name="llm-main")
    assert first_connection is not None
    pinned = first_factory._bind_llm_credential(
        conversation_id="conversation-1",
        connection=first_connection,
    )
    assert pinned.selected_api_key_index == 0
    assert pinned.api_key == "key-a"

    assert pinned.commit_credential_retry(selected_api_key_index=1, expected_index=0) is True

    # A separate repository/factory models a restarted process or another pod.
    resumed_runtime = DatabaseRuntime(database_url)
    second_factory = _binding_factory(
        connector_repository,
        LlmCredentialBindingRepository(resumed_runtime),
    )
    resumed_connection = connector_repository.get_connection(
        workspace_id="ws-1",
        name="llm-main",
    )
    assert resumed_connection is not None
    resumed = second_factory._bind_llm_credential(
        conversation_id="conversation-1",
        connection=resumed_connection,
    )
    assert resumed.selected_api_key_index == 1
    assert resumed.api_key == "key-b"


def test_session_binding_treats_empty_credential_set_as_missing_connector(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path / 'empty_credentials.db'}")
    runtime.create_schema()
    connector_repository = LlmConnectorRepository(
        db=runtime,
        secret_store=InMemorySecretStore(),
    )
    connector_repository.upsert(
        UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.test/v1",
                model="test-model",
            ),
        )
    )

    binding_repository = LlmCredentialBindingRepository(runtime)

    def fail_resolve(**_kwargs: object) -> int:
        raise AssertionError("empty credentials must not reach binding resolution")

    monkeypatch.setattr(binding_repository, "resolve", fail_resolve)
    factory = _binding_factory(connector_repository, binding_repository)
    connection = connector_repository.get_connection(workspace_id="ws-1", name="llm-main")

    assert connection is not None
    assert factory._bind_llm_credential(
        conversation_id="conversation-1",
        connection=connection,
    ) is None

    factory_without_binding_repository = OrchestrationSessionFactory(
        llm_connector_repository=connector_repository,
        conversation_manager=SimpleNamespace(),
        db_connector_repository=SimpleNamespace(),
        turn_builder=SimpleNamespace(),
        sql_router=SimpleNamespace(),
    )
    unbound_connection = connector_repository.get_connection(
        workspace_id="ws-1",
        name="llm-main",
    )
    assert unbound_connection is not None
    assert factory_without_binding_repository._bind_llm_credential(
        conversation_id="conversation-1",
        connection=unbound_connection,
    ) is None

    session_factory = OrchestrationSessionFactory(
        llm_connector_repository=connector_repository,
        conversation_manager=SimpleNamespace(),
        db_connector_repository=SimpleNamespace(list=lambda **_kwargs: []),
        turn_builder=SimpleNamespace(
            inspect_workspace=lambda **_kwargs: WorkspaceToolingSnapshot(
                has_db_connectors=False,
                rag_configured=False,
            )
        ),
        sql_router=SimpleNamespace(),
        llm_credential_binding_repository=binding_repository,
    )
    session = session_factory.build(
        ChatAskRequest(
            question="Покажи выручку",
            workspace_id="ws-1",
            conversation_id="conversation-1",
            llm_connector_name="llm-main",
        )
    )
    assert session.llm_connection is None
