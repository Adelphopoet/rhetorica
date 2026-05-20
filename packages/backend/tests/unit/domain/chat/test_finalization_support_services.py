from __future__ import annotations

from rhetorica_backend.contracts.chat import OptimizationTrace
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


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


def test_final_payload_builder_service_returns_contract_fallback_payload() -> None:
    payload = FinalPayloadBuilderService.fallback_final_payload()

    assert payload["answer"].startswith("I could not produce a schema-valid answer.")
    assert payload["uncertainty_flags"] == ["schema_validation_failed"]


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
