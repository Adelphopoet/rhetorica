from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from rhetorica_backend.infra.postgres.agent_session import (
    AgentSessionConversationNotFoundError,
    PostgresAgentSessionStore,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import event


def _session_store(tmp_path: Path):
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/agent_session.db")

    @event.listens_for(runtime.engine, "connect")
    def enable_foreign_keys(
        connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    runtime.create_schema()
    repository = ChatRepository(runtime)
    conversation = repository.create_conversation(workspace_id="ws-1", title="Session")
    return (
        runtime,
        repository,
        conversation,
        PostgresAgentSessionStore(
            runtime,
            conversation.owner_user_id,
        ),
    )


def test_agent_session_bootstraps_legacy_history_once(tmp_path: Path) -> None:
    _, repository, conversation, store = _session_store(tmp_path)
    repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="legacy question",
        owner_user_id=conversation.owner_user_id,
    )
    repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="legacy answer",
        owner_user_id=conversation.owner_user_id,
    )

    agent_session = store.for_conversation(conversation.id)
    assert asyncio.run(agent_session.get_items()) == [
        {"role": "user", "content": "legacy question"},
        {"role": "assistant", "content": "legacy answer"},
    ]

    repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="must not be bootstrapped twice",
        owner_user_id=conversation.owner_user_id,
    )
    assert asyncio.run(store.for_conversation(conversation.id).get_items())[-1] == {
        "role": "assistant",
        "content": "legacy answer",
    }


def test_agent_session_supports_sdk_history_operations_and_atomic_replace(
    tmp_path: Path,
) -> None:
    _, _, conversation, store = _session_store(tmp_path)
    agent_session = store.for_conversation(conversation.id)

    asyncio.run(
        agent_session.add_items([
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ])
    )
    assert asyncio.run(agent_session.get_items(limit=1)) == [
        {"role": "assistant", "content": "two"}
    ]
    assert asyncio.run(agent_session.pop_item()) == {
        "role": "assistant",
        "content": "two",
    }

    checkpoint = {"role": "user", "content": "Rhetorica context checkpoint v1"}
    asyncio.run(agent_session.replace_items([checkpoint]))
    assert asyncio.run(agent_session.get_items()) == [checkpoint]

    asyncio.run(agent_session.clear_session())
    assert asyncio.run(agent_session.get_items()) == []
    assert asyncio.run(store.for_conversation(conversation.id).get_items()) == []


def test_agent_session_enforces_conversation_owner(tmp_path: Path) -> None:
    runtime, _, conversation, _ = _session_store(tmp_path)

    with pytest.raises(AgentSessionConversationNotFoundError):
        PostgresAgentSessionStore(runtime, "different-owner").for_conversation(conversation.id)
