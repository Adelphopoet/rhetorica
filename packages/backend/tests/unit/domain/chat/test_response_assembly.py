from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    OptimizationTrace,
)
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.chat.orchestration.response_assembly import (
    AssembledChatTurn,
    ChatResponseAssembler,
)
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
from rhetorica_backend.domain.results.visualization_planner import (
    VisualizationPlanner,
)


def _execution(
    execution_id: str,
    columns: list[str],
    rows: list[list[Any]],
) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id=execution_id,
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT * FROM execution_result",
        row_count=len(rows),
        warnings=[],
        violations=[],
        preview=QueryPreview(columns=columns, rows=rows),
    )


def test_response_assembler_persists_runtime_context_inside_capability_trace() -> None:
    saved_messages: list[dict[str, object]] = []

    def save_message(**kwargs):
        saved_messages.append(kwargs)
        if kwargs["role"] == "assistant":
            return SimpleNamespace(id="assistant-1")
        return SimpleNamespace(id="user-1")

    assembler = ChatResponseAssembler(
        conversation_manager=SimpleNamespace(save_message=save_message),
        visualization_planner=VisualizationPlanner(),
        analysis_document_builder=AnalysisDocumentBuilder(VisualizationPlanner()),
    )

    assistant_message_id = assembler.save_to_history(
        conversation_id="conv-1",
        request=ChatAskRequest(question="What happened to orders?", workspace_id="ws-1"),
        turn=AssembledChatTurn(
            answer="Нормально.",
            generated_sql="SELECT 1",
            selected_database_connector="warehouse",
            metadata_sources=["mcp"],
            context_tables=["dm.orders"],
            runtime_context={"metadata_table_columns": {"dm.orders": ["order_id"]}},
            claims=[],
            evidence=[],
            claim_evidence_links=[],
            uncertainty_flags=[],
            capability_trace=CapabilityTrace(available_tools=["fetch_metadata_tables"]),
            optimization_trace=OptimizationTrace(),
            execution=None,
            analysis_document=None,
            visualization=None,
            warnings=[],
        ),
    )

    assert assistant_message_id == "assistant-1"
    assert saved_messages[0]["role"] == "user"
    assert saved_messages[1]["role"] == "assistant"
    assert saved_messages[1]["capability_trace"]["runtime_context"] == {
        "metadata_table_columns": {"dm.orders": ["order_id"]}
    }


def test_select_connector_name_prefers_payload_selected_connector() -> None:
    connector_name = ChatResponseAssembler.select_connector_name(
        request=ChatAskRequest(question="What happened to orders?", workspace_id="ws-1"),
        llm_payload={"selected_database_connector": "trino-adhoc"},
        db_connectors=[SimpleNamespace(name="greenplum")],
    )

    assert connector_name == "trino-adhoc"


def test_assemble_turn_uses_primary_execution_only_after_finalizer_failure() -> None:
    assembler = ChatResponseAssembler(
        conversation_manager=SimpleNamespace(save_message=lambda **_: None),
        visualization_planner=VisualizationPlanner(),
        analysis_document_builder=AnalysisDocumentBuilder(VisualizationPlanner()),
    )
    daily = _execution(
        "exec-daily",
        ["order_date", "orders_count"],
        [["2026-05-01", 329990], ["2026-05-02", 299906]],
    )
    summary = _execution(
        "exec-summary",
        ["min_date", "max_date", "total_orders"],
        [["2026-05-01", "2026-05-05", 629896]],
    )

    turn = assembler.assemble_turn(
        llm_payload={
            "answer": "| Дата | Заказы |\n|---|---|\n| 2026-05-01 | 329 990 |",
            "execution": daily.model_dump(mode="json"),
            "successful_executions": [
                daily.model_dump(mode="json"),
                summary.model_dump(mode="json"),
            ],
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        warnings=["llm_finalizer_failed"],
        capability_surface=SimpleNamespace(
            available_tools=[],
            degraded_capabilities=[],
            policy_decisions=[],
        ),
        selected_connector_name="warehouse",
    )

    assert turn.execution is not None
    assert turn.execution.execution_id == "exec-daily"
    assert turn.analysis_document is None
