from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import scenarios as scenario_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.contracts import ChatAskResponse
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import ChatOrchestrator


def _client(tmp_path: Path) -> TestClient:
    settings.database_url = f"sqlite:///{tmp_path}/scenario_api.db"
    get_chat_service_factory.cache_clear()

    from rhetorica_backend.domain.execution import build_execution_service

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    _ = scenario_routes
    return TestClient(create_app())


def test_scenario_routes_start_empty_then_create_and_archive_personal_scenario(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    list_response = client.get("/v1/scenarios?workspace_id=default")
    assert list_response.status_code == 200
    assert list_response.json() == []

    create_response = client.post(
        "/v1/scenarios",
        json={
            "workspace_id": "default",
            "scope": "personal",
            "title": "Weekly retention review",
            "summary": "My private workflow",
            "description": "Check cohorts",
            "prompt_template": "Review {{time_window}} retention for {{segment}}.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "time_window": {"type": "string"},
                    "segment": {"type": "string", "default": "platform"},
                },
                "required": ["time_window"],
            },
            "tool_policy": {
                "allow_run_sql": True,
                "sql_guard_required": True,
                "allowed_tools": [],
                "post_answer_checklist": [],
            },
            "default_connector_bindings": {"run_sql": True},
            "status": "published",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["scope"] == "personal"

    archive_response = client.delete(
        f"/v1/scenarios/{created['id']}?workspace_id=default"
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"


def test_scenario_run_creates_traceable_conversation(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)

    create_response = client.post(
        "/v1/scenarios",
        json={
            "workspace_id": "default",
            "scope": "personal",
            "title": "Conversion drilldown",
            "summary": "Check where funnel broke",
            "description": "Run a funnel investigation",
            "prompt_template": "Inspect {{funnel_name}} for {{time_window}}.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "funnel_name": {"type": "string"},
                    "time_window": {"type": "string"},
                },
                "required": ["funnel_name", "time_window"],
            },
            "tool_policy": {
                "allow_run_sql": False,
                "sql_guard_required": True,
                "allowed_tools": [],
                "post_answer_checklist": [],
            },
            "default_connector_bindings": {"run_sql": False},
            "status": "published",
        },
    )
    scenario_id = create_response.json()["id"]

    monkeypatch.setattr(
        ChatOrchestrator,
        "ask",
        lambda _self, payload: ChatAskResponse(
            answer=f"Ran: {payload.question}",
            conversation_id=payload.conversation_id,
            warnings=[],
            metadata_sources=[],
            context_tables=[],
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            assistant_message_id="msg-1",
        ),
    )

    run_response = client.post(
        f"/v1/scenarios/{scenario_id}/run",
        json={
            "workspace_id": "default",
            "inputs": {
                "funnel_name": "checkout",
                "time_window": "2026-W14",
            },
        },
    )
    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["scenario_run_id"]
    assert payload["conversation_id"]
    assert payload["rendered_prompt"] == "Inspect checkout for 2026-W14."
