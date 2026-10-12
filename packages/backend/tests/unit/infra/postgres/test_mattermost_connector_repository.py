from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rhetorica_backend.contracts.admin_integrations import (
    MattermostConnectionConfig,
    UpsertMattermostConnectorRequest,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)
from rhetorica_backend.infra.postgres.models import (
    MattermostEventLedgerOrm,
    MattermostThreadBindingOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select


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
            agent_id="agent-1",
            connection=MattermostConnectionConfig(
                provider="mattermost",
                base_url="https://mattermost.example.com",
                bot_token="secret-token",
                bot_username="@rhetorica",
                verify_tls=False,
                listen_enabled=True,
                send_sql_enabled=True,
                ambient_listen_enabled=True,
                ambient_channel_ids=["channel-1", "channel-1", "channel-2"],
                ambient_decision_prompt="Join analytics discussions only.",
            ),
        )
    )

    assert created.connection.bot_token is None
    assert created.connection.bot_username == "rhetorica"
    assert created.connection.send_sql_enabled is True
    assert created.connection.ambient_listen_enabled is True
    assert created.connection.ambient_channel_ids == ["channel-1", "channel-2"]
    assert created.connection.ambient_decision_prompt == "Join analytics discussions only."
    assert created.agent_id == "agent-1"
    assert created.has_bot_token is True

    connection = repository.get_connection(workspace_id="ws-1", name="tag-main")
    assert connection is not None
    assert connection.bot_token == "secret-token"
    assert connection.verify_tls is False
    assert connection.send_sql_enabled is True
    assert connection.ambient_channel_ids == ["channel-1", "channel-2"]

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


def test_mattermost_connector_does_not_send_sql_by_default(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "mattermost_send_sql_default.db")
    repository = MattermostConnectorRepository(db=runtime)

    created = repository.upsert(
        UpsertMattermostConnectorRequest(
            name="tag-main",
            workspace_id="ws-1",
            connection=MattermostConnectionConfig(
                base_url="https://mattermost.example.com",
            ),
        )
    )

    assert created.connection.send_sql_enabled is False


def test_mattermost_event_ledger_claims_each_post_once(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "mattermost_ledger.db")
    repository = MattermostConnectorRepository(db=runtime)

    assert repository.claim_event(
        workspace_id="ws-1",
        connector_id="connector-1",
        channel_id="channel-1",
        post_id="post-1",
        trigger="ambient",
    ) is True
    assert repository.claim_event(
        workspace_id="ws-1",
        connector_id="connector-1",
        channel_id="channel-1",
        post_id="post-1",
        trigger="ambient",
    ) is False

    assert repository.claim_event(
        workspace_id="ws-1",
        connector_id="connector-1",
        channel_id="channel-1",
        post_id="post-2",
        trigger="ambient",
    ) is True

    repository.complete_event(
        connector_id="connector-1",
        post_id="post-2",
        status="ignored",
        reason="not relevant",
    )

    with runtime.session() as session:
        rows = (
            session.execute(
                select(MattermostEventLedgerOrm).order_by(MattermostEventLedgerOrm.post_id)
            )
            .scalars()
            .all()
        )
    assert [row.post_id for row in rows] == ["post-1", "post-2"]
    assert rows[0].status == "processing"
    assert rows[1].status == "ignored"
    assert rows[1].reason == "not relevant"


def test_mattermost_event_ledger_lists_responded_posts_for_conversation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "mattermost_responded_posts.db")
    repository = MattermostConnectorRepository(db=runtime)

    for post_id in ("post-1", "post-2", "post-3"):
        assert repository.claim_event(
            workspace_id="ws-1",
            connector_id="connector-1",
            channel_id="channel-1",
            post_id=post_id,
            trigger="mention",
        )
    repository.complete_event(
        connector_id="connector-1",
        post_id="post-1",
        status="responded",
        conversation_id="conversation-1",
    )
    repository.complete_event(
        connector_id="connector-1",
        post_id="post-2",
        status="ignored",
        conversation_id="conversation-1",
    )
    repository.complete_event(
        connector_id="connector-1",
        post_id="post-3",
        status="responded",
        conversation_id="conversation-2",
    )

    assert repository.list_conversation_post_ids(
        connector_id="connector-1",
        conversation_id="conversation-1",
    ) == {"post-1"}


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
                base_url="https://mattermost.example.com",
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


def test_get_or_create_binding_rotates_expired_binding(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "mattermost_expired_binding.db")
    repository = MattermostConnectorRepository(db=runtime, binding_ttl_seconds=60)
    chat_repository = ChatRepository(db=runtime)
    connector = repository.upsert(
        UpsertMattermostConnectorRequest(
            name="tag-main",
            workspace_id="ws-1",
            connection=MattermostConnectionConfig(
                provider="mattermost",
                base_url="https://mattermost.example.com",
                bot_token="secret-token",
            ),
        )
    )
    old_conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="Tag old",
    )
    new_conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="Tag new",
    )

    first = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=connector.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: old_conversation.id,
    )
    with runtime.session() as session:
        binding = session.execute(select(MattermostThreadBindingOrm)).scalar_one()
        binding.updated_at = datetime.now(tz=UTC) - timedelta(minutes=5)
        session.commit()

    second = repository.get_or_create_binding(
        workspace_id="ws-1",
        connector_id=connector.id,
        channel_id="channel-1",
        thread_key="thread-1",
        conversation_id_factory=lambda: new_conversation.id,
        conversation_exists=lambda conversation_id: chat_repository.get_conversation(
            conversation_id
        )
        is not None,
    )

    assert first == old_conversation.id
    assert second == new_conversation.id
