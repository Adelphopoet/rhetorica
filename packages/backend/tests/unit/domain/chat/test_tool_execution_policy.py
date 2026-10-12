from __future__ import annotations

from rhetorica_backend.domain.chat.orchestration.models import (
    ExecuteToolCallPlan,
    RejectRepeatedToolCallPlan,
)
from rhetorica_backend.domain.chat.orchestration.orchestration_service_bundles import (
    build_tool_execution_policy,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)


def _policy() -> ToolExecutionPolicy:
    return build_tool_execution_policy()


def test_plan_tool_call_executes_matching_call_twice_then_returns_error() -> None:
    state = ToolOrchestrationState()
    args_json = '{"sql": "SELECT 1"}'
    signature = ("run_sql", args_json)
    policy = _policy()

    for execution_number in (1, 2):
        plan = policy.plan_tool_call(
            state=state,
            tool_name="run_sql",
            args_json=args_json,
            tool_call_id=f"call-{execution_number}",
        )
        assert isinstance(plan, ExecuteToolCallPlan)
        state.record_executed_tool_call(
            tool_call_id=plan.tool_call_id,
            tool_name="run_sql",
            arguments={"sql": "SELECT 1"},
            result={"ok": True, "execution_id": f"exec-{execution_number}"},
            prompt_result={
                "tool_artifact": {
                    "artifact_id": "artifact-select-1",
                    "sha256": "same-result-sha",
                }
            },
        )

    for execution_number in (1, 2):
        state.record_executed_tool_call(
            tool_call_id=f"other-{execution_number}",
            tool_name="run_sql",
            arguments={"sql": "SELECT 2"},
            result={"ok": True},
            prompt_result={
                "tool_artifact": {
                    "artifact_id": "artifact-select-2",
                    "sha256": "other-result-sha",
                }
            },
        )

    third_plan = policy.plan_tool_call(
        state=state,
        tool_name="run_sql",
        args_json=args_json,
        tool_call_id="call-3",
    )

    assert isinstance(third_plan, RejectRepeatedToolCallPlan)
    assert third_plan.call_signature == signature
    tool_result = third_plan.tool_result
    assert tool_result["ok"] is False
    assert tool_result["error_code"] == "identical_tool_call_limit_reached"
    assert tool_result["error_category"] == "NON_RETRYABLE"
    assert tool_result["can_continue"] is True
    assert tool_result["retryable"] is False
    assert tool_result["retry_recommended"] is False
    assert tool_result["blocked_for_turn"] is True
    assert tool_result["duplicate"] is True
    assert tool_result["new_evidence"] is False
    assert tool_result["previous_execution_count"] == 2
    assert tool_result["previous_artifact_ids"] == ["artifact-select-1"]
    assert tool_result["previous_result_sha256s"] == ["same-result-sha"]
    assert tool_result["previous_results_identical"] is True
    assert tool_result["blocked_call_count"] == 2
    assert [item["arguments"] for item in tool_result["blocked_call_signatures"]] == [
        {"sql": "SELECT 1"},
        {"sql": "SELECT 2"},
    ]
    assert tool_result["tool_name"] == "run_sql"
    assert tool_result["arguments_json"] == args_json
    assert "результат идентичен, новых данных нет" in tool_result["user_message"]
    assert "не повторяйте его" in tool_result["user_message"]
    assert state.warnings == ["llm_tool_loop_identical_call_limit_reached"]


def test_plan_tool_call_executes_same_tool_with_changed_arguments() -> None:
    state = ToolOrchestrationState()
    for execution_number in (1, 2):
        state.record_executed_tool_call(
            tool_call_id=f"call-{execution_number}",
            tool_name="catalog__search",
            arguments={"query": "raw%"},
            result={"ok": True},
            prompt_result={},
        )

    plan = _policy().plan_tool_call(
        state=state,
        tool_name="catalog__search",
        args_json='{"query": "raw% expected_income"}',
        tool_call_id="call-3",
    )

    assert isinstance(plan, ExecuteToolCallPlan)
    assert state.warnings == []


def test_repeated_call_error_does_not_claim_unknown_results_are_identical() -> None:
    state = ToolOrchestrationState()
    for execution_number in (1, 2):
        state.record_executed_tool_call(
            tool_call_id=f"call-{execution_number}",
            tool_name="catalog__search",
            arguments={"query": "raw%"},
            result={"ok": True},
            prompt_result={},
        )

    plan = _policy().plan_tool_call(
        state=state,
        tool_name="catalog__search",
        args_json='{"query": "raw%"}',
        tool_call_id="call-3",
    )

    assert isinstance(plan, RejectRepeatedToolCallPlan)
    assert plan.tool_result["previous_results_identical"] is None
    assert "результат идентичен" not in plan.tool_result["user_message"]
    assert "повторное выполнение не запущено" in plan.tool_result["user_message"]
