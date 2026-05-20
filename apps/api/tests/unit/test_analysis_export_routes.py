from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from rhetorica_api.app.main import create_app
from rhetorica_api.app.routes import analysis_exports as analysis_exports_routes
from rhetorica_api.app.routes import confluence_connectors as confluence_connector_routes
from rhetorica_api.app.routes import connectors as connector_routes
from rhetorica_api.app.routes import execution as execution_routes
from rhetorica_api.app.routes import llm_connectors as llm_connector_routes
from rhetorica_api.app.routes import openmetadata_connectors as openmetadata_connector_routes
from rhetorica_api.app.settings import settings
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository


def _client(tmp_path: Path) -> tuple[TestClient, ChatRepository]:
    settings.database_url = f"sqlite:///{tmp_path}/api_analysis_export.db"
    execution_routes._runtime.cache_clear()
    connector_routes._repository.cache_clear()
    llm_connector_routes._repository.cache_clear()
    confluence_connector_routes._repository.cache_clear()
    openmetadata_connector_routes._repository.cache_clear()
    analysis_exports_routes._runtime.cache_clear()
    analysis_exports_routes._celery_app.cache_clear()

    from rhetorica_backend.domain.execution import build_execution_service

    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()

    return TestClient(create_app()), ChatRepository(db=db_runtime)


def test_analysis_export_route_returns_html_snapshot(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default",
        title="Export thread",
    )
    chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Show weekly GMV.",
    )
    assistant = chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="GMV grew 25% week over week.",
        generated_sql="select week, gmv from metrics.weekly_gmv",
        selected_connector="clickhouse-main",
        visualization_spec={
            "version": "v1",
            "kind": "bar",
            "goal": "comparison",
            "title": "Weekly GMV",
            "summary": "GMV by week.",
            "description": "Bar chart built from preview.",
            "planner_mode": "rule_fallback",
            "confidence_score": 0.84,
            "chart_safe_on_preview": True,
            "x_axis_label": "week",
            "y_axis_label": "gmv",
            "value_format": "currency",
            "metric_unit": "RUB",
            "data_source": {
                "type": "execution_preview",
                "execution_id": "exec-1",
                "row_count": 0,
                "is_truncated": False,
            },
            "encoding": {"x": "week", "y": ["gmv"], "series": None},
            "transform": {"sort": None, "limit": 50, "aggregation": "none"},
            "annotations": [],
            "warnings": [],
            "rationale": {"why_kind": "Clear weekly comparison.", "why_fields": ["week", "gmv"]},
        },
    )

    response = client.get(
        f"/v1/chat/conversation/{conversation.id}/message/{assistant.id}/export/html"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment;" in response.headers["content-disposition"]
    assert "Weekly GMV" in response.text


def test_analysis_export_route_encodes_unicode_filename(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default",
        title="Export thread",
    )
    chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="количество заказов по дням за апрель 2026",
    )
    assistant = chat_repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="Готово.",
    )

    response = client.get(
        f"/v1/chat/conversation/{conversation.id}/message/{assistant.id}/export/ipynb"
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert 'filename="' in disposition
    assert ".ipynb" in disposition
    assert "filename*=UTF-8''%D0%BA%D0%BE%D0%BB%D0%B8%D1%87%D0%B5%D1%81%D1%82%D0%B2%D0%BE" in disposition


def test_analysis_export_route_rejects_user_message(tmp_path: Path) -> None:
    client, chat_repository = _client(tmp_path)
    conversation = chat_repository.create_conversation(
        workspace_id="default",
        title="Export thread",
    )
    message = chat_repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="Show weekly GMV.",
    )

    response = client.get(
        f"/v1/chat/conversation/{conversation.id}/message/{message.id}/export/html"
    )

    assert response.status_code == 400
