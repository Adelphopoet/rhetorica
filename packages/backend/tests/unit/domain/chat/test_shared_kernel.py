from __future__ import annotations

from rhetorica_backend.contracts.chat import SemanticGrounding, SemanticGroundingCandidate
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)


def test_build_finalizer_context_does_not_include_chartability_hints() -> None:
    execution = ExecuteSqlResponse(
        execution_id="chart-exec-2",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(
            columns=["order_dt", "order_cnt"],
            rows=[["2026-05-01", 455], ["2026-05-02", 462]],
        ),
        row_count=2,
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT 1",
        selected_database_connector="warehouse",
        metadata_sources=set(),
        context_tables=[],
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT dt, SUM(cnt) FROM t GROUP BY dt",
                connector_name="warehouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )
    context = shared_kernel.build_finalizer_context(
        raw_final_text="Done",
        state=state,
    )
    assert "execution" not in context
    assert "turn_context" not in context
    assert context["execution_summary"][0]["execution_id"] == "chart-exec-2"
    assert "has_chartable_execution" not in context
    assert all("is_chartable" not in summary for summary in context["execution_summary"])


def test_execution_summaries_from_state_do_not_include_is_chartable() -> None:
    chartable_data = ExecuteSqlResponse(
        execution_id="chart-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(
            columns=["dt", "cnt"],
            rows=[["2026-05-01", 455], ["2026-05-02", 462]],
        ),
        row_count=2,
    )
    non_chartable = ExecuteSqlResponse(
        execution_id="no-chart-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(
            columns=["min_val", "max_val"],
            rows=[["2026-05-01", "2026-05-06"]],
        ),
        row_count=1,
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT 1",
        selected_database_connector="warehouse",
        metadata_sources=set(),
        context_tables=[],
        execution=non_chartable,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT dt, SUM(cnt) FROM t",
                connector_name="warehouse",
                execution=chartable_data,
                preflight_status="pass",
            ),
            SqlExecutionAttempt(
                sql="SELECT MIN(dt), MAX(dt) FROM t",
                connector_name="warehouse",
                execution=non_chartable,
                preflight_status="pass",
            ),
        ],
    )
    summaries = shared_kernel.execution_summaries_from_state(state, include_chartable=True)
    assert len(summaries) == 2
    assert all("is_chartable" not in summary for summary in summaries)


def test_shared_kernel_build_finalizer_context_includes_semantic_grounding() -> None:
    state = ToolOrchestrationState(
        generated_sql="SELECT count() FROM dm.sales_orders",
        selected_database_connector="warehouse",
        metadata_sources={"openmetadata"},
        context_tables=["warehouse.analytics.dm.sales_orders"],
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="warehouse.analytics.dm.sales_orders",
            selection_rationale="Selected governed source.",
            candidates=[
                SemanticGroundingCandidate(
                    fqn="warehouse.analytics.dm.sales_orders",
                    service="warehouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact"],
                    reason_codes=["governance_strength"],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=10,
                    suspicious=False,
                )
            ],
        ),
    )

    context = shared_kernel.build_finalizer_context(
        raw_final_text="Готово",
        state=state,
    )
    assert context["semantic_grounding"]["chosen_table_fqn"] == (
        "warehouse.analytics.dm.sales_orders"
    )

def test_shared_kernel_llm_request_failed_payload_uses_latest_successful_execution() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT 1",
        selected_database_connector="warehouse",
        metadata_sources={"runtime"},
        context_tables=["warehouse.default.dm_orders"],
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 1",
                connector_name="warehouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    payload = shared_kernel.llm_request_failed_payload(state)
    assert payload["selected_database_connector"] == "warehouse"
    assert payload["sql"] == "SELECT 1"
    assert payload["execution"]["execution_id"] == "exec-1"


def test_deterministic_finalizer_answer_uses_execution_preview_not_internal_instruction() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(
            columns=["order_date", "order_count"],
            rows=[["2026-05-01", 10], ["2026-05-02", 12]],
        ),
    )
    state = ToolOrchestrationState(
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT order_date, order_count FROM dm.orders",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    answer = shared_kernel.deterministic_finalizer_answer(state)

    assert "Use execution" not in answer
    assert "grounded result" not in answer
    assert "| order_date | order_count |" in answer
    assert "| 2026-05-01 | 10 |" in answer


def test_shared_kernel_semantic_note_appends_note() -> None:
    state = ToolOrchestrationState(
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="warehouse.analytics.dm.sales_orders",
            selection_rationale="Selected governed source.",
            candidates=[
                SemanticGroundingCandidate(
                    fqn="warehouse.analytics.dm.sales_orders",
                    service="warehouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact"],
                    reason_codes=[
                        "governance_strength",
                        "business_layer_preference",
                        "preferred_connector_map_match",
                    ],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=42,
                    suspicious=False,
                ),
                SemanticGroundingCandidate(
                    fqn="warehouse.analytics.raw.sales_orders",
                    service="warehouse",
                    database="analytics",
                    schema="raw",
                    relevance_score=87.0,
                    matched_on=["name_exact"],
                    reason_codes=[],
                    certification=None,
                    tier=None,
                    owners=[],
                    domains=[],
                    usage_count=1,
                    suspicious=False,
                ),
            ],
        )
    )

    context = shared_kernel.runtime_context_payload_from_state(state)
    assert context["semantic_grounding"]["chosen_table_fqn"] == (
        "warehouse.analytics.dm.sales_orders"
    )
