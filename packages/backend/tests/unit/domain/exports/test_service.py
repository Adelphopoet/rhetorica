from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image
from rhetorica_backend.contracts.execution import (
    ExecuteSqlResponse,
    ExecutionDetailResponse,
    ExecutionHistoryItem,
    QueryPreview,
)
from rhetorica_backend.domain.exports import AnalysisExportService
from rhetorica_backend.infra.postgres.chat_repository import ChatMessageItem

_TEST_PNG_BUFFER = BytesIO()
Image.new("RGB", (16, 9), "#ffffff").save(_TEST_PNG_BUFFER, format="PNG")
_TEST_PNG = _TEST_PNG_BUFFER.getvalue()


class _StaticChartRenderer:
    def render(self, request: object) -> bytes:
        return _TEST_PNG


def _service() -> AnalysisExportService:
    return AnalysisExportService(chart_renderer=_StaticChartRenderer())


def _assistant_message() -> ChatMessageItem:
    return ChatMessageItem(
        message_id="msg-1",
        conversation_id="conv-1",
        role="assistant",
        content="GMV grew 25% week over week.",
        created_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
        generated_sql="select week, gmv from metrics.weekly_gmv",
        execution_id="exec-1",
        metadata_sources=["dbt", "mcp"],
        context_tables=["metrics.weekly_gmv"],
        claims=[
            {
                "claim_id": "claim-1",
                "text": "GMV grew 25% week over week.",
                "status": "verified",
            }
        ],
        evidence=[
            {
                "evidence_id": "evidence-1",
                "type": "execution_result",
                "reference": "exec-1",
                "status": "verified",
            }
        ],
        claim_evidence_links=[{"claim_id": "claim-1", "evidence_ids": ["evidence-1"]}],
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
                "row_count": 2,
                "is_truncated": False,
            },
            "encoding": {"x": "week", "y": ["gmv"], "series": None},
            "transform": {"sort": None, "limit": 50, "aggregation": "none"},
            "annotations": [],
            "warnings": [],
            "rationale": {"why_kind": "Clear weekly comparison.", "why_fields": ["week", "gmv"]},
        },
    )


def _execution_detail() -> ExecutionDetailResponse:
    preview = QueryPreview(columns=["week", "gmv"], rows=[["2026-W13", 1200], ["2026-W14", 1500]])
    history = ExecutionHistoryItem(
        execution_id="exec-1",
        workspace_id="default",
        engine="clickhouse",
        status="succeeded",
        submitted_sql="select week, gmv from metrics.weekly_gmv",
        normalized_sql="select week, gmv from metrics.weekly_gmv",
        timeout_ms=30_000,
        row_limit=500,
        row_count=2,
        duration_ms=140,
        query_id="query-1",
        error_message=None,
        warnings=[],
        violations=[],
        preview=preview,
        connection_summary={"host": "localhost"},
        created_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
        started_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
        cancel_requested_at=None,
    )
    result = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        query_id="query-1",
        normalized_sql="select week, gmv from metrics.weekly_gmv",
        row_count=2,
        duration_ms=140,
        preview=preview,
    )
    return ExecutionDetailResponse(history=history, result=result)


def test_export_service_builds_bundle_from_snapshot() -> None:
    service = _service()

    bundle = service.build_bundle(
        workspace_id="default",
        conversation_id="conv-1",
        assistant_message=_assistant_message(),
        question="Show weekly GMV.",
        execution_detail=_execution_detail(),
    )

    assert bundle.question == "Show weekly GMV."
    assert bundle.execution_summary.execution_id == "exec-1"
    assert bundle.preview.columns == ["week", "gmv"]
    assert bundle.visualization is not None


def test_export_service_renders_html_notebook_and_pdf() -> None:
    service = _service()
    bundle = service.build_bundle(
        workspace_id="default",
        conversation_id="conv-1",
        assistant_message=_assistant_message(),
        question="Show weekly GMV.",
        execution_detail=_execution_detail(),
    )

    html = service.render_html(bundle)
    assert "Weekly GMV" in html
    assert "Immutable snapshot" in html

    notebook = json.loads(service.render_notebook(bundle))
    assert notebook["nbformat"] == 4
    assert any("Question" in "".join(cell["source"]) for cell in notebook["cells"])

    pdf = service.render_pdf(bundle)
    assert pdf.startswith(b"%PDF")


def test_export_service_renders_static_snapshots_for_rich_chart_kinds() -> None:
    service = _service()

    for kind in ("histogram", "waterfall"):
        assistant = _assistant_message()
        assert assistant.visualization_spec is not None
        assistant.visualization_spec["version"] = "v2"
        assistant.visualization_spec["kind"] = kind
        assistant.visualization_spec["family"] = (
            "distribution" if kind == "histogram" else "composition"
        )

        bundle = service.build_bundle(
            workspace_id="default",
            conversation_id="conv-1",
            assistant_message=assistant,
            question="Show weekly GMV.",
            execution_detail=_execution_detail(),
        )

        html = service.render_html(bundle)
        assert "data:image/png;base64," in html
        assert "Weekly GMV" in html


def test_export_service_repairs_stale_visualization_and_renders_markdown_table() -> None:
    service = _service()
    assistant = ChatMessageItem(
        message_id="msg-2",
        conversation_id="conv-1",
        role="assistant",
        content=(
            "Запрос выполнен.\n\n"
            "**Количество заказов по дням за апрель 2026:**\n\n"
            "| Дата | Количество заказов |\n"
            "|------|-------------------|\n"
            "| 2026-04-01 | 391 062 |\n"
            "| 2026-04-02 | 375 104 |"
        ),
        created_at=datetime(2026, 4, 4, 11, 0, tzinfo=UTC),
        generated_sql="select order_date, order_count from dm.ft_order",
        execution_id="exec-2",
        visualization_spec={
            "version": "v2",
            "kind": "area_stacked",
            "goal": "trend",
            "title": "Whs Code over Order Create Dttm",
            "summary": "Peak whs code is somewhere else.",
            "description": "Bad stale chart.",
            "planner_mode": "rule_fallback",
            "confidence_score": 0.62,
            "chart_safe_on_preview": False,
            "x_axis_label": "Order Create Dttm",
            "y_axis_label": "Whs Code",
            "value_format": "integer",
            "metric_unit": None,
            "data_source": {
                "type": "execution_preview",
                "execution_id": "wrong-exec",
                "row_count": 2,
                "is_truncated": False,
            },
            "encoding": {"x": "order_create_dttm", "y": ["whs_code"], "series": "ecom_order_pk"},
            "transform": {"sort": "asc", "limit": 50, "aggregation": "none"},
            "annotations": [],
            "warnings": [],
            "rationale": {"why_kind": "Bad stale chart.", "why_fields": ["wrong"]},
        },
    )
    preview = QueryPreview(
        columns=["order_date", "order_count"],
        rows=[["2026-04-01", 391062], ["2026-04-02", 375104]],
    )
    history = ExecutionHistoryItem(
        execution_id="exec-2",
        workspace_id="default",
        engine="clickhouse",
        status="succeeded",
        submitted_sql="select order_date, order_count from dm.ft_order",
        normalized_sql="select order_date, order_count from dm.ft_order",
        timeout_ms=30_000,
        row_limit=500,
        row_count=2,
        duration_ms=330,
        query_id="query-2",
        error_message=None,
        warnings=[],
        violations=[],
        preview=preview,
        connection_summary={"host": "localhost"},
        created_at=datetime(2026, 4, 4, 11, 0, tzinfo=UTC),
        updated_at=datetime(2026, 4, 4, 11, 0, tzinfo=UTC),
        started_at=datetime(2026, 4, 4, 11, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 4, 11, 0, tzinfo=UTC),
        cancel_requested_at=None,
    )
    result = ExecuteSqlResponse(
        execution_id="exec-2",
        status="succeeded",
        engine="clickhouse",
        query_id="query-2",
        normalized_sql="select order_date, order_count from dm.ft_order",
        row_count=2,
        duration_ms=330,
        preview=preview,
    )

    bundle = service.build_bundle(
        workspace_id="default",
        conversation_id="conv-1",
        assistant_message=assistant,
        question="количество заказов по дням за апрель 2026 из dm.ft_order",
        execution_detail=ExecutionDetailResponse(history=history, result=result),
    )

    assert bundle.visualization is None

    html = service.render_html(bundle)
    assert "<table>" in html
    assert "<strong>Количество заказов по дням за апрель 2026:</strong>" in html
    assert "data:image/png;base64," not in html
    assert "Whs Code over Order Create Dttm" not in html

    notebook = json.loads(service.render_notebook(bundle))
    assert not any(
        "data:image/png;base64," in "".join(cell["source"]) for cell in notebook["cells"]
    )
