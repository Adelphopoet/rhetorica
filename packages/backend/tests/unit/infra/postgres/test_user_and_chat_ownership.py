from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage
from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm, ChatMessageOrm
from rhetorica_backend.infra.postgres.chat_repository import (
    ChatRepository,
    ConversationNotFoundError,
)
from rhetorica_backend.infra.postgres.models import UserAccountOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import (
    ADMIN_ROLE,
    BOOTSTRAP_AUTH_PROVIDER,
    BOOTSTRAP_AUTH_SUBJECT,
    LOCAL_AUTH_PROVIDER,
    UserAccountRepository,
)
from rhetorica_backend.infra.security.passwords import hash_password
from sqlalchemy import func, select


def _runtime(tmp_path: Path, db_name: str) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/{db_name}")
    runtime.create_schema()
    return runtime


def test_user_repository_reuses_single_bootstrap_user_per_workspace(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "bootstrap_user.db")
    repository = UserAccountRepository(db=runtime)

    first = repository.ensure_bootstrap_user("default")
    second = repository.ensure_bootstrap_user("default")

    assert first.id == second.id
    assert second.login == "bootstrap-admin"
    assert second.role == ADMIN_ROLE
    assert second.auth_provider == BOOTSTRAP_AUTH_PROVIDER
    assert second.auth_subject == BOOTSTRAP_AUTH_SUBJECT
    assert second.is_bootstrap_user is True
    assert second.last_seen_at is not None


def test_chat_repository_scopes_conversations_by_owner_user(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "chat_owner_scope.db")
    user_repository = UserAccountRepository(db=runtime)
    chat_repository = ChatRepository(db=runtime)

    bootstrap_user = user_repository.ensure_bootstrap_user("ws-1")
    other_user_id = str(uuid4())
    now = datetime.now(tz=UTC)
    with runtime.session() as session:
        session.add(
            UserAccountOrm(
                id=other_user_id,
                workspace_id="ws-1",
                login="other-analyst",
                password_hash=hash_password("other-pass-123"),
                role=ADMIN_ROLE,
                is_active=True,
                auth_provider=LOCAL_AUTH_PROVIDER,
                auth_subject="local-user:subject-2",
                email="other@example.com",
                display_name="Other Analyst",
                is_bootstrap_user=False,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )
        session.commit()

    bootstrap_conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="bootstrap",
    )
    other_conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="other",
        owner_user_id=other_user_id,
    )

    assert bootstrap_conversation.owner_user_id == bootstrap_user.id
    assert other_conversation.owner_user_id == other_user_id

    scoped_list = chat_repository.list_conversations(
        workspace_id="ws-1",
        owner_user_id=bootstrap_user.id,
    )
    assert [item.id for item in scoped_list] == [bootstrap_conversation.id]

    assert (
        chat_repository.get_conversation(
            other_conversation.id,
            owner_user_id=bootstrap_user.id,
        )
        is None
    )


def test_chat_repository_soft_deletes_conversation_without_removing_data(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "chat_soft_delete.db")
    chat_repository = ChatRepository(db=runtime)
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="retain me",
    )
    message = chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Keep this history",
        owner_user_id=conversation.owner_user_id,
    )

    assert chat_repository.delete_conversation(
        conversation.id,
        owner_user_id=conversation.owner_user_id,
    )
    assert not chat_repository.delete_conversation(
        conversation.id,
        owner_user_id=conversation.owner_user_id,
    )
    assert chat_repository.get_conversation(conversation.id) is None
    assert chat_repository.get_conversation_with_messages(conversation.id) is None
    assert chat_repository.get_messages(conversation.id) == []
    assert chat_repository.list_conversations("ws-1") == []
    assert chat_repository.update_conversation_title(conversation.id, "zombie") is None
    with pytest.raises(ConversationNotFoundError):
        chat_repository.save_message(
            conversation_id=conversation.id,
            role="user",
            content="This must not be stored",
        )

    with runtime.session() as session:
        stored_conversation = session.get(ChatConversationOrm, conversation.id)
        stored_messages = session.execute(
            select(func.count(ChatMessageOrm.id)).where(
                ChatMessageOrm.conversation_id == conversation.id
            )
        ).scalar_one()

    assert stored_conversation is not None
    assert stored_conversation.deleted_at is not None
    assert stored_messages == 1
    assert message.content == "Keep this history"


def test_chat_repository_persists_conversation_state(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "chat_conversation_state.db")
    chat_repository = ChatRepository(db=runtime)
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="state",
    )

    saved = chat_repository.update_conversation_state(
        conversation_id=conversation.id,
        conversation_state={
            "active_metrics": ["revenue"],
            "state_revision": 1,
            "semantic_context": {"scope": "revenue", "status": "active"},
        },
    )
    loaded = chat_repository.get_conversation_state(conversation.id)
    with_messages = chat_repository.get_conversation_with_messages(conversation.id)

    assert saved["active_metrics"] == ["revenue"]
    assert loaded["semantic_context"]["scope"] == "revenue"
    assert with_messages is not None
    assert with_messages.conversation_state["state_revision"] == 1


def test_chat_repository_persists_and_lists_source_message_ids(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "chat_source_message_ids.db")
    chat_repository = ChatRepository(db=runtime)
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="Mattermost source ids",
    )

    message = chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Current external question",
        source_provider="mattermost",
        source_message_id="post-42",
    )
    chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Web question",
        source_provider="web",
        source_message_id="web-1",
    )

    assert message.source_provider == "mattermost"
    assert message.source_message_id == "post-42"
    assert chat_repository.list_source_message_ids(
        conversation_id=conversation.id,
        source_provider="mattermost",
    ) == {"post-42"}


def test_chat_repository_atomically_accumulates_conversation_token_usage(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "chat_token_usage.db")
    chat_repository = ChatRepository(db=runtime)
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="usage",
    )

    chat_repository.add_token_usage(
        conversation_id=conversation.id,
        token_usage=ConversationTokenUsage(
            input_tokens=100,
            output_tokens=20,
            cached_input_tokens=70,
            cache_write_input_tokens=10,
            cache_usage_reported_calls=1,
        ),
    )
    chat_repository.add_token_usage(
        conversation_id=conversation.id,
        token_usage=ConversationTokenUsage(input_tokens=30, output_tokens=5),
    )
    chat_repository.update_conversation_state(
        conversation_id=conversation.id,
        conversation_state={"state_revision": 2},
    )

    loaded = chat_repository.get_conversation(conversation.id)
    listed = chat_repository.list_conversations("ws-1")

    assert loaded is not None
    assert loaded.token_usage == ConversationTokenUsage(
        input_tokens=130,
        output_tokens=25,
        cached_input_tokens=70,
        cache_write_input_tokens=10,
        cache_usage_reported_calls=1,
    )
    assert loaded.token_usage.total_tokens == 155
    assert loaded.conversation_state == {"state_revision": 2}
    assert listed[0].token_usage == loaded.token_usage


def test_chat_repository_replaces_and_scopes_agent_owned_tool_evidence(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "chat_tool_evidence.db")
    user_repository = UserAccountRepository(db=runtime)
    chat_repository = ChatRepository(db=runtime)
    owner = user_repository.ensure_bootstrap_user("ws-1")
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="evidence",
        owner_user_id=owner.id,
    )
    now = datetime.now(tz=UTC)
    arguments = {"entityType": "metric", "fqn": "Month-To-Date Orders"}

    first = chat_repository.upsert_tool_evidence(
        conversation_id=conversation.id,
        tool_name="catalog__get_entity_details",
        arguments=arguments,
        payload={"ok": True, "result": {"version": 1}},
        note="version 1",
        content_sha256="a" * 64,
        source_char_count=100,
        observed_at=now,
        owner_user_id=owner.id,
    )
    second = chat_repository.upsert_tool_evidence(
        conversation_id=conversation.id,
        tool_name="catalog__get_entity_details",
        arguments=dict(reversed(list(arguments.items()))),
        payload={"ok": True, "result": {"version": 2}},
        note="version 2",
        content_sha256="b" * 64,
        source_char_count=120,
        observed_at=now,
        owner_user_id=owner.id,
    )

    assert first.evidence_key == second.evidence_key
    assert first.id == second.id
    records = chat_repository.list_tool_evidence(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
    )
    assert len(records) == 1
    assert records[0].note == "version 2"
    assert records[0].payload["result"]["version"] == 2
    assert (
        chat_repository.get_tool_evidence(
            conversation_id=conversation.id,
            evidence_key=second.evidence_key,
            owner_user_id="not-the-owner",
        )
        is None
    )
    assert chat_repository.delete_tool_evidence(
        conversation_id=conversation.id,
        evidence_key=second.evidence_key,
        owner_user_id=owner.id,
    )
    assert (
        chat_repository.list_tool_evidence(
            conversation_id=conversation.id,
            owner_user_id=owner.id,
        )
        == []
    )


def test_chat_repository_retains_recent_tool_artifacts_with_ttl_and_scope(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, "chat_tool_artifacts.db")
    user_repository = UserAccountRepository(db=runtime)
    chat_repository = ChatRepository(db=runtime)
    owner = user_repository.ensure_bootstrap_user("ws-1")
    conversation = chat_repository.create_conversation(
        workspace_id="ws-1",
        title="artifacts",
        owner_user_id=owner.id,
    )
    now = datetime.now(tz=UTC)

    first = chat_repository.save_tool_artifact(
        conversation_id=conversation.id,
        artifact_id=f"ta_{'a' * 32}",
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        payload={"ok": True, "rows": [[1]]},
        content_sha256="a" * 64,
        source_char_count=32,
        observed_at=now,
        expires_at=now + timedelta(days=7),
        max_records=1,
        owner_user_id=owner.id,
    )
    second = chat_repository.save_tool_artifact(
        conversation_id=conversation.id,
        artifact_id=f"ta_{'b' * 32}",
        tool_name="run_sql",
        arguments={"sql": "SELECT 2"},
        payload={"ok": True, "rows": [[2]]},
        content_sha256="b" * 64,
        source_char_count=32,
        observed_at=now + timedelta(seconds=1),
        expires_at=now + timedelta(days=7),
        max_records=1,
        owner_user_id=owner.id,
    )

    assert first.artifact_id != second.artifact_id
    records = chat_repository.list_tool_artifacts(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        limit=20,
    )
    assert [record.artifact_id for record in records] == [second.artifact_id]
    assert records[0].payload["rows"] == [[2]]
    assert (
        chat_repository.get_tool_artifact(
            conversation_id=conversation.id,
            artifact_id=second.artifact_id,
            owner_user_id="not-the-owner",
        )
        is None
    )
    assert (
        chat_repository.clear_tool_artifacts(
            conversation_id=conversation.id,
            owner_user_id=owner.id,
        )
        == 1
    )
