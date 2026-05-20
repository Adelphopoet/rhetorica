from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import chat as chat_routes
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings
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
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()

    from rhetorica_backend.domain.execution import build_execution_service

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    return TestClient(create_app()), ChatRepository(db=db_runtime)


def test_conversation_endpoints_crud(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    create_response = client.post(
        "/v1/chat/conversation",
        json={"workspace_id": "default", "title": "Test Conversation"},
    )
    assert create_response.status_code == 200
    conversation = create_response.json()
    conversation_id = conversation["id"]

    get_response = client.get(f"/v1/chat/conversation/{conversation_id}")
    assert get_response.status_code == 200
    assert get_response.json()["messages"] == []

    list_response = client.get("/v1/chat/conversations", params={"workspace_id": "default"})
    assert list_response.status_code == 200
    assert any(item["id"] == conversation_id for item in list_response.json())

    delete_response = client.delete(f"/v1/chat/conversation/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted", "conversation_id": conversation_id}

    missing_response = client.get(f"/v1/chat/conversation/{conversation_id}")
    assert missing_response.status_code == 404


def test_message_feedback_endpoint_updates_assistant_message(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(workspace_id="default", title="Feedback thread")
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
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message_id"] == assistant.id
    assert payload["conversation_id"] == conversation.id
    assert payload["feedback"] == "wrong"
    assert payload["feedback_updated_at"] is not None


def test_message_feedback_useful_enqueues_rag_pipeline(tmp_path: Path, monkeypatch) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(workspace_id="default", title="Useful answer")
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
