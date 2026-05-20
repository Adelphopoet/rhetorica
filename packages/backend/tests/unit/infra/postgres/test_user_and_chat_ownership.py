from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
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
            "analysis_checkpoint": {"run_id": "run-1", "status": "running"},
        },
    )
    loaded = chat_repository.get_conversation_state(conversation.id)
    with_messages = chat_repository.get_conversation_with_messages(conversation.id)

    assert saved["active_metrics"] == ["revenue"]
    assert loaded["analysis_checkpoint"]["run_id"] == "run-1"
    assert with_messages is not None
    assert with_messages.conversation_state["state_revision"] == 1
