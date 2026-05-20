from __future__ import annotations

from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import (
    ChatOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    DuplicateToolCallPlan,
    StopToolResultDecision,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)


def _policy() -> ToolExecutionPolicy:
    return ChatOrchestrator._build_tool_execution_policy()


def test_plan_tool_call_reuses_cached_duplicate_and_requests_recovery() -> None:
    state = ToolOrchestrationState(
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 1",
                connector_name="clickhouse",
                execution=ExecuteSqlResponse(
                    execution_id="exec-1",
                    status="succeeded",
                    engine="clickhouse",
                    normalized_sql="SELECT 1",
                ),
                preflight_status="pass",
            )
        ]
    )
    args_json = '{"sql": "SELECT 1"}'
    signature = ("run_sql", args_json)
    state.previous_tool_calls.append(signature)
    state.tool_call_context_revisions[signature] = state.metadata_context_revision
    state.tool_call_results[signature] = {
        "ok": False,
        "error_code": "sql_preflight_failed",
        "can_continue": True,
    }

    plan = _policy().plan_tool_call(
        state=state,
        tool_name="run_sql",
        args_json=args_json,
        tool_call_id="call-1",
    )

    assert isinstance(plan, DuplicateToolCallPlan)
    assert plan.cached_result_available is True
    assert plan.recovery_kind == "duplicate_loop"
    assert plan.tool_result["ok"] is True
    assert plan.tool_result["duplicate"] is True
    assert plan.tool_result["new_evidence"] is False
    assert plan.tool_result["cached_result"] == {
        "ok": False,
        "error_code": "sql_preflight_failed",
        "can_continue": True,
    }
    assert "llm_tool_loop_duplicate_detected" in state.warnings
    assert "llm_tool_loop_duplicate_reused_result" in state.warnings
    assert "llm_tool_loop_duplicate_preflight_fail" in state.warnings


def test_resolve_tool_result_builds_fatal_payload_from_latest_success() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-good",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 42",
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT broken()",
        selected_database_connector="fallback",
        preflight_fail=2,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 42",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    decision = _policy().resolve_tool_result(
        tool_result={
            "ok": False,
            "can_continue": True,
            "error_code": "sql_preflight_failed",
            "user_message": "SQL preflight failed.",
            "preflight_errors": [{"message": "Column 'gmv' is missing in table 'dm.ft_orders'."}],
        },
        state=state,
    )

    assert isinstance(decision, StopToolResultDecision)
    assert decision.recovery_kind == "execution_error"
    assert decision.payload["sql"] == "SELECT 42"
    assert decision.payload["selected_database_connector"] == "clickhouse"
    assert decision.payload["execution"]["execution_id"] == "exec-good"
    assert "Column 'gmv' is missing" in decision.payload["answer"]
    assert "llm_tool_loop_preflight_fail_stop" in decision.payload["warnings"]


def test_build_incomplete_payload_reuses_latest_successful_execution() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-good",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT count() FROM dm.ft_order",
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT broken()",
        selected_database_connector="fallback",
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT count() FROM dm.ft_order",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    payload = _policy().build_incomplete_payload(state)

    assert payload["answer"] == "Analysis incomplete but query results are available."
    assert payload["sql"] == "SELECT count() FROM dm.ft_order"
    assert payload["selected_database_connector"] == "clickhouse"
    assert payload["execution"]["execution_id"] == "exec-good"


def test_build_incomplete_payload_without_successful_attempts() -> None:
    state = ToolOrchestrationState(
        generated_sql="SELECT broken()",
        selected_database_connector="fallback",
        execution_attempts=[],
    )

    payload = _policy().build_incomplete_payload(state)

    assert payload["answer"] == "Model did not finish tool orchestration."
    assert payload["sql"] == "SELECT broken()"
    assert payload["selected_database_connector"] == "fallback"


def test_build_incomplete_payload_with_custom_answer() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-good",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT count() FROM dm.ft_order",
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT broken()",
        selected_database_connector="fallback",
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT count() FROM dm.ft_order",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    payload = _policy().build_incomplete_payload(state, answer="Custom answer content")

    assert payload["answer"] == "Custom answer content"
    assert payload["sql"] == "SELECT count() FROM dm.ft_order"
