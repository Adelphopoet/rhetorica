from __future__ import annotations

from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    MattermostConnectionConfig,
    UpsertMattermostConnectorRequest,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_mattermost_connector_upsert_get_connection_and_binding(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "mattermost_repo.db")
    repository = MattermostConnectorRepository(db=runtime)

    created = repository.upsert(
        UpsertMattermostConnectorRequest(
            name="tag-main",
            workspace_id="ws-1",
            connection=MattermostConnectionConfig(
                provider="mattermost",
                base_url="https://chat.example.com",
                bot_token="secret-token",
                bot_username="@rhetorica",
                verify_tls=False,
                listen_enabled=True,
            ),
        )
    )

    assert created.connection.bot_token is None
    assert created.connection.bot_username == "rhetorica"
    assert created.has_bot_token is True

    connection = repository.get_connection(workspace_id="ws-1", name="tag-main")
    assert connection is not None
    assert connection.bot_token == "secret-token"
    assert connection.verify_tls is False

    chat_repository = ChatRepository(db=runtime)
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="Tag test",
    )
    first = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=created.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: conversation.id,
    )
    second = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=created.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: "unused",
    )
    assert first == conversation.id
    assert second == conversation.id

    deleted = repository.delete(connector_id=created.id, workspace_id="ws-1")
    assert deleted is True
    assert repository.get_connection(workspace_id="ws-1", name="tag-main") is None


def test_get_or_create_binding_repairs_missing_conversation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "mattermost_stale_binding.db")
    repository = MattermostConnectorRepository(db=runtime)
    chat_repository = ChatRepository(db=runtime)
    connector = repository.upsert(
        UpsertMattermostConnectorRequest(
            name="tag-main",
            workspace_id="ws-1",
            connection=MattermostConnectionConfig(
                provider="mattermost",
                base_url="https://chat.example.com",
                bot_token="secret-token",
            ),
        )
    )

    stale = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=connector.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: "missing-conversation",
    )
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="Tag repaired",
    )
    repaired = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=connector.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: conversation.id,
        conversation_exists=lambda conversation_id: chat_repository.get_conversation(
            conversation_id
        )
        is not None,
    )

    assert stale == "missing-conversation"
    assert repaired == conversation.id
