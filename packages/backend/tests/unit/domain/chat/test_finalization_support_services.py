from __future__ import annotations

from rhetorica_backend.contracts.chat import OptimizationTrace
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.contracts.visualization import (
    VisualizationDataSource,
    VisualizationEncoding,
    VisualizationRationale,
    VisualizationSpec,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


def _execution_result() -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="trino",
        normalized_sql="select day, revenue, orders from dm.daily",
        row_count=2,
        warnings=[],
        violations=[],
        preview=QueryPreview(
            columns=["day", "revenue", "orders"],
            rows=[["2026-05-01", 100, 10], ["2026-05-02", 150, 12]],
        ),
    )


def _visualization(kind: str, y: str) -> VisualizationSpec:
    return VisualizationSpec(
        kind=kind,
        title=f"{y} by day",
        summary=f"{y} trend",
        data_source=VisualizationDataSource(
            execution_id="exec-1",
            row_count=2,
            is_truncated=False,
        ),
        encoding=VisualizationEncoding(x="day", y=[y]),
        rationale=VisualizationRationale(why_kind="explicit chart request"),
    )


def test_final_payload_builder_service_preserves_finalizer_answer() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)

    payload = service.build_final_payload_from_parsed(
        parsed_payload={"answer": "Выручка растет.", "claims": [], "evidence": []},
        fallback_answer=None,
        state=ToolOrchestrationState(
            optimization_trace=OptimizationTrace(),
            prompt_version="test",
        ),
    )

    assert payload["answer"] == "Выручка растет."


def test_final_payload_builder_service_uses_answer_markdown_contract_field() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)

    payload = service.build_final_payload_from_parsed(
        parsed_payload={
            "schema_version": "v1",
            "answer_markdown": "Привет! Чем могу помочь?",
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        fallback_answer='{"schema_version":"v1","answer_markdown":"не надо так"}',
        state=ToolOrchestrationState(
            optimization_trace=OptimizationTrace(),
            prompt_version="test",
        ),
    )

    assert payload["answer"] == "Привет! Чем могу помочь?"


def test_final_payload_builder_service_builds_document_for_multiple_visualizations() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)
    state = ToolOrchestrationState(
        optimization_trace=OptimizationTrace(),
        prompt_version="test",
    )
    state.record_execution_outcome(
        sql="select day, revenue, orders from dm.daily",
        connector_name="trino",
        execution=_execution_result(),
    )
    state.visualization_candidates.extend(
        [_visualization("bar", "revenue"), _visualization("line", "orders")]
    )
    state.visualization_candidate = state.visualization_candidates[-1]

    payload = service.build_final_payload_from_parsed(
        parsed_payload={
            "schema_version": "v1",
            "answer_markdown": "Показал два графика.",
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
            "visualization": None,
        },
        fallback_answer=None,
        state=state,
    )

    assert payload["analysis_document"]["document_version"] == "v1"
    chart_blocks = [
        block for block in payload["analysis_document"]["blocks"] if block["kind"] == "chart"
    ]
    assert [block["visualization"]["kind"] for block in chart_blocks] == ["bar", "line"]
    assert [block["visualization"]["encoding"]["y"] for block in chart_blocks] == [
        ["revenue"],
        ["orders"],
    ]


def test_final_payload_builder_replaces_unsupported_evidence_with_documentation_evidence() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)
    state = ToolOrchestrationState(
        optimization_trace=OptimizationTrace(),
        prompt_version="test",
        tool_call_count=1,
    )
    state.metadata_sources.add("documentation")
    state.record_tool_result(
        tool_name="read_documentation_page",
        arguments={"page_ref": "/platform_docs/docs/dbt/"},
        result={
            "ok": True,
            "page": {
                "page_ref": "/platform_docs/docs/dbt/",
                "title": "DBT Welcome Guide",
                "content": "Для dbt-модели нужны SQL и YAML файлы.",
            },
        },
    )

    payload = service.build_final_payload_from_parsed(
        parsed_payload={
            "schema_version": "v1",
            "answer_markdown": "## Создание витрины через dbt\n\nНужны SQL и YAML.",
            "sql": None,
            "target": None,
            "claims": [
                {
                    "claim_id": "claim_1",
                    "text": "Для dbt-модели нужны SQL и YAML файлы.",
                    "status": "verified",
                }
            ],
            "evidence": [
                {
                    "evidence_id": "bad_evidence",
                    "type": "airflow_runtime",
                    "reference": "platform_docs",
                    "title": "DBT Welcome Guide",
                    "status": "verified",
                }
            ],
            "claim_evidence_links": [
                {"claim_id": "claim_1", "evidence_ids": ["bad_evidence"]},
            ],
            "uncertainty_flags": [],
        },
        fallback_answer=None,
        state=state,
    )

    assert payload["answer"].startswith("## Создание витрины")
    assert payload["evidence"][0]["type"] == "internal_documentation"
    assert payload["evidence"][0]["reference"] == "/platform_docs/docs/dbt/"
    assert payload["claim_evidence_links"] == [
        {
            "claim_id": "claim_1",
            "evidence_ids": [payload["evidence"][0]["evidence_id"]],
        }
    ]


def test_final_payload_builder_service_ignores_legacy_visualization_tool_output() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)
    state = ToolOrchestrationState(
        optimization_trace=OptimizationTrace(),
        prompt_version="test",
    )
    state.tool_call_results[("build_visualization", "{'kind': 'line'}")] = {
        "ok": True,
        "tool_name": "build_visualization",
        "visualization": {
            "version": "v2",
            "kind": "line",
            "data_source": {
                "execution_id": "exec-1",
                "row_count": 5,
                "is_truncated": False,
            },
            "encoding": {"x": "report_date", "y": ["orders_count"]},
            "transform": {
                "limit": 50,
                "aggregation": "none",
                "normalize": "none",
                "cumulative": False,
            },
            "rationale": {
                "why_kind": "Линейный график подходит для дневного тренда.",
                "why_fields": ["report_date", "orders_count"],
            },
        },
    }

    payload = service.build_final_payload_from_parsed(
        parsed_payload={"answer": "Выручка растет.", "claims": [], "evidence": []},
        fallback_answer=None,
        state=state,
    )

    assert payload["visualization"] is None


def test_final_payload_builder_service_preserves_explicit_final_visualization() -> None:
    service = FinalPayloadBuilderService(normalize_answer=lambda *, answer, state: answer)
    visualization = {
        "version": "v2",
        "kind": "line",
        "data_source": {
            "execution_id": "exec-1",
            "row_count": 5,
            "is_truncated": False,
        },
        "encoding": {"x": "report_date", "y": ["orders_count"]},
        "transform": {
            "limit": 50,
            "aggregation": "none",
            "normalize": "none",
            "cumulative": False,
        },
        "rationale": {
            "why_kind": "Линейный график подходит для дневного тренда.",
            "why_fields": ["report_date", "orders_count"],
        },
    }

    payload = service.build_final_payload_from_parsed(
        parsed_payload={
            "answer": "Выручка растет.",
            "claims": [],
            "evidence": [],
            "visualization": visualization,
        },
        fallback_answer=None,
        state=ToolOrchestrationState(
            optimization_trace=OptimizationTrace(),
            prompt_version="test",
        ),
    )

    assert payload["visualization"] == visualization
