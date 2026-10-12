from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import chat as chat_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository


class _FakeTaskQueue:
    def __init__(self) -> None:
        self.sent_tasks: list[dict[str, object]] = []

    def send_task(self, name: str, kwargs: dict[str, object]) -> None:
        self.sent_tasks.append({"name": name, "kwargs": kwargs})


def _client(tmp_path: Path) -> tuple[TestClient, ChatRepository]:
    settings.database_url = f"sqlite:///{tmp_path}/api_chat_history.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()

    from rhetorica_backend.domain.execution import build_execution_service

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    return TestClient(create_app()), ChatRepository(db=db_runtime)


def test_conversation_endpoints_crud(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)

    create_response = client.post(
        "/v1/chat/conversation",
        json={"workspace_id": "default", "title": "Test Conversation"},
    )
    assert create_response.status_code == 200
    conversation = create_response.json()
    conversation_id = conversation["id"]
    assert conversation["token_usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "cache_usage_reported_calls": 0,
        "total_tokens": 0,
    }

    chat_repository.add_token_usage(
        conversation_id=conversation_id,
        token_usage=ConversationTokenUsage(
            input_tokens=120,
            output_tokens=30,
            cached_input_tokens=90,
            cache_write_input_tokens=12,
            cache_usage_reported_calls=1,
        ),
    )

    get_response = client.get(f"/v1/chat/conversation/{conversation_id}")
    assert get_response.status_code == 200
    assert get_response.json()["messages"] == []
    assert get_response.json()["token_usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": 90,
        "cache_write_input_tokens": 12,
        "cache_usage_reported_calls": 1,
        "total_tokens": 150,
    }

    list_response = client.get("/v1/chat/conversations", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    listed = next(item for item in list_response.json() if item["id"] == conversation_id)
    assert listed["token_usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": 90,
        "cache_write_input_tokens": 12,
        "cache_usage_reported_calls": 1,
        "total_tokens": 150,
    }

    delete_response = client.delete(f"/v1/chat/conversation/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted", "conversation_id": conversation_id}

    missing_response = client.get(f"/v1/chat/conversation/{conversation_id}")
    assert missing_response.status_code == 404

    list_after_delete = client.get("/v1/chat/conversations", params={"workspace_id": "default"})
    assert all(item["id"] != conversation_id for item in list_after_delete.json())

    repeated_delete = client.delete(f"/v1/chat/conversation/{conversation_id}")
    assert repeated_delete.status_code == 404


def test_conversation_detail_upgrades_legacy_workspace_skill_trace(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default", title="Legacy skill trace"
    )
    chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Legacy answer",
        capability_trace={
            "runtime_context": {
                "workspace_skills_read": [
                    {
                        "id": "skill-1",
                        "name": "analyses-playbook",
                        "content_sha256": "a" * 64,
                        "content_markdown": "# Private legacy skill content",
                    }
                ]
            }
        },
    )

    response = client.get(f"/v1/chat/conversation/{conversation.id}")

    assert response.status_code == 200
    runtime_context = response.json()["messages"][0]["capability_trace"]["runtime_context"]
    assert runtime_context["workspace_skill_activations"] == [
        {
            "skill_id": "skill-1",
            "content_sha256": "a" * 64,
            "scope": "turn",
            "activated_by": "model",
        }
    ]
    assert "workspace_skills_read" not in runtime_context
    assert "Private legacy skill content" not in response.text


def test_message_feedback_endpoint_updates_assistant_message(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default", title="Feedback thread"
    )
    assistant = chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Retention dropped in week 14.",
    )

    response = client.post(
        f"/v1/chat/message/{assistant.id}/feedback",
        json={
            "workspace_id": "default",
            "conversation_id": conversation.id,
            "feedback": "wrong",
            "what_went_wrong": "The calculation uses the wrong period.",
            "expected_answer": "Compare the same fiscal period.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message_id"] == assistant.id
    assert payload["conversation_id"] == conversation.id
    assert payload["feedback"] == "wrong"
    assert payload["feedback_updated_at"] is not None
    assert payload["what_went_wrong"] == "The calculation uses the wrong period."
    assert payload["expected_answer"] == "Compare the same fiscal period."


def test_message_feedback_rejects_incomplete_wrong_feedback(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(workspace_id="default", title="Feedback")
    assistant = chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Answer",
    )

    response = client.post(
        f"/v1/chat/message/{assistant.id}/feedback",
        json={
            "workspace_id": "default",
            "conversation_id": conversation.id,
            "feedback": "wrong",
            "what_went_wrong": "   ",
            "expected_answer": "Expected answer",
        },
    )

    assert response.status_code == 422


def test_message_feedback_useful_enqueues_rag_pipeline(tmp_path: Path, monkeypatch) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default", title="Useful answer"
    )
    assistant = chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Morning push notifications improve retention.",
    )
    queue = _FakeTaskQueue()
    monkeypatch.setattr(chat_routes, "_task_queue", lambda: queue)

    response = client.post(
        f"/v1/chat/message/{assistant.id}/feedback",
        json={
            "workspace_id": "default",
            "conversation_id": conversation.id,
            "feedback": "useful",
        },
    )

    assert response.status_code == 200
    assert queue.sent_tasks == [
        {
            "name": "rag_insights.process_feedback",
            "kwargs": {
                "payload": {
                    "workspace_id": "default",
                    "conversation_id": conversation.id,
                    "message_id": assistant.id,
                    "rag_connector_name": None,
                }
            },
        }
    ]


def test_message_feedback_rejects_user_message(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(workspace_id="default", title="User message")
    user_message = chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="What changed?",
    )

    response = client.post(
        f"/v1/chat/message/{user_message.id}/feedback",
        json={
            "workspace_id": "default",
            "conversation_id": conversation.id,
            "feedback": "useful",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Feedback is allowed only for assistant messages"


def test_history_search_pages_and_persisted_journal(tmp_path: Path) -> None:
    client, repository = _client(tmp_path)
    ids = []
    for index in range(29):
        response = client.post("/v1/chat/conversation", json={"title": f"History {index}"})
        assert response.status_code == 200
        ids.append(response.json()["id"])
    owner = repository.get_conversation(ids[0]).owner_user_id
    events = [
        {"type": "tool_call_start", "timestamp": "2026-10-06T08:00:00Z", "tool_name": "repository_read", "tool_call_id": "read", "arguments": {"path": "models/net_revenue.sql"}},
        {"type": "tool_call_end", "timestamp": "2026-10-06T08:00:01Z", "tool_name": "repository_read", "tool_call_id": "read", "success": True, "result_summary": "Read file"},
    ]
    saved = repository.save_message(ids[0], "assistant", "needle_100% net_revenue", journal_events=events)
    first = client.get("/v1/chat/conversations", params={"limit": 24}).json()
    second = client.get("/v1/chat/conversations", params={"limit": 24, "offset": 24}).json()
    assert len(first) == 24 and len(second) == 5
    assert {item["id"] for item in first}.isdisjoint(item["id"] for item in second)
    assert {item["id"] for item in first + second} == set(ids)
    assert [item["id"] for item in client.get("/v1/chat/conversations", params={"search": "NEEDLE_100%"}).json()] == [ids[0]]
    assert client.get("/v1/chat/conversations", params={"search": "needleX100"}).json() == []
    assert repository.list_conversations("default", owner_user_id="someone-else", search="net_revenue") == []
    detail = client.get(f"/v1/chat/conversation/{ids[0]}").json()
    assert next(item for item in detail["messages"] if item["id"] == saved.id)["journal_events"] == events
    repository.delete_conversation(ids[0], owner_user_id=owner)
    assert client.get("/v1/chat/conversations", params={"search": "net_revenue"}).json() == []
    assert client.get("/v1/chat/conversations", params={"offset": -1}).status_code == 422
    assert client.get("/v1/chat/conversations", params={"limit": 101}).status_code == 422
