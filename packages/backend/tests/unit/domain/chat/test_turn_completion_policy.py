from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisPlan,
    AnalysisPlanStep,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    AbortDecision,
    FinalAnswerDecision,
    NonToolTurnCompletionInput,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.turn_completion_policy import (
    TurnCompletionPolicy,
)

_VALID_VISUALIZATION = {
    "version": "v2",
    "kind": "line",
    "data_source": {
        "execution_id": "exec-1",
        "row_count": 5,
        "is_truncated": False,
    },
    "encoding": {
        "x": "report_date",
        "y": ["orders_count"],
    },
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


def _policy(
    *,
    build_final_payload_from_parsed=None,
    fallback_final_payload=None,
    finalize_raw_answer=None,
    has_needs_user_clarification_flag=None,
    incomplete_tool_orchestration_payload=None,
    is_acceptable_final_payload=None,
    validate_or_repair_final_payload=None,
) -> TurnCompletionPolicy:
    return TurnCompletionPolicy(
        build_final_payload_from_parsed=build_final_payload_from_parsed
        or (lambda **_: {"answer": "built"}),
        fallback_final_payload=fallback_final_payload
        or (lambda: {"answer": "fallback"}),
        finalize_raw_answer=finalize_raw_answer or (lambda **_: {"answer": "structured"}),
        has_needs_user_clarification_flag=has_needs_user_clarification_flag
        or (lambda _: False),
        incomplete_tool_orchestration_payload=incomplete_tool_orchestration_payload
        or (lambda state, answer=None: {"answer": "incomplete", "warnings": list(state.warnings)}),
        is_acceptable_final_payload=is_acceptable_final_payload
        or (lambda **_: True),
        strip_thinking_tags=lambda content: content.replace("<think>", "").replace(
            "</think>", ""
        ),
        to_optional_str=lambda value: (
            value.strip()
            if isinstance(value, str) and value.strip()
            else None
        ),
        validate_or_repair_final_payload=validate_or_repair_final_payload
        or (lambda **_: {"answer": "validated", "uncertainty_flags": []}),
    )


def test_turn_completion_policy_dispatches_direct_final_answer() -> None:
    policy = _policy()
    state = ToolOrchestrationState()

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ignored",
            finalizable_content="ignored",
        ),
        decision=FinalAnswerDecision(
            raw_final_text="Hello",
            finalization_mode="direct",
        ),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="hi"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "decision_finalized"
    assert completion.decision_type == "final_answer"
    assert completion.payload == {"answer": "direct"}


def test_turn_completion_policy_uses_deterministic_path_for_execution_markdown() -> None:
    from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
    from rhetorica_backend.domain.chat.orchestration.state import SqlExecutionAttempt

    def fail_finalize_raw_answer(**_: Any) -> dict[str, Any]:
        raise AssertionError("LLM finalizer must not be called for execution markdown")

    policy = _policy(
        finalize_raw_answer=fail_finalize_raw_answer,
        is_acceptable_final_payload=lambda **_: True,
    )
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(columns=["group", "revenue"], rows=[["milk", 10]]),
    )
    state = ToolOrchestrationState(
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT group, revenue FROM dm.sales",
                connector_name="warehouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )
    raw_answer = "| group | revenue |\n| --- | ---: |\n| milk | 10 |"

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content=raw_answer,
            finalizable_content=raw_answer,
            has_current_tools=True,
            current_tool_names=["run_sql"],
        ),
        decision=FinalAnswerDecision(raw_final_text=raw_answer),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="show sales"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **kwargs: {
            "answer": kwargs["raw_final_text"],
            "uncertainty_flags": [],
        },
    )

    assert completion.completion_type == "decision_finalized"
    assert completion.payload["answer"] == raw_answer


def test_turn_completion_policy_blocks_final_answer_without_plan_evidence() -> None:
    policy = _policy()
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Find root cause",
            status="running",
            steps=[
                AnalysisPlanStep(
                    id="baseline",
                    title="Build baseline",
                    status="done",
                )
            ],
            required_evidence=["exec-required"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ignored",
            finalizable_content="ignored",
        ),
        decision=FinalAnswerDecision(
            raw_final_text="Done too early",
            finalization_mode="direct",
        ),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "analysis_plan_incomplete"
    assert "analysis_plan_required_evidence_missing" in state.warnings
    assert "analysis_plan_missing_evidence:exec-required" in state.warnings


def test_turn_completion_policy_blocks_final_answer_with_planned_plan_steps() -> None:
    policy = _policy()
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Find root cause",
            status="running",
            steps=[
                AnalysisPlanStep(
                    id="baseline",
                    title="Build baseline",
                    status="done",
                    evidence_ids=["execution_result", "table_proof"],
                ),
                AnalysisPlanStep(
                    id="breakdown",
                    title="Optional breakdown",
                    status="planned",
                ),
            ],
            required_evidence=["execution_result", "table_proof"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ignored",
            finalizable_content="ignored",
        ),
        decision=FinalAnswerDecision(
            raw_final_text="Done with table evidence",
            finalization_mode="direct",
        ),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="prove with tables why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "analysis_plan_incomplete"
    assert state.analysis_plan is not None
    assert state.analysis_plan.status == "running"
    assert "analysis_plan_incomplete_steps:breakdown" in state.warnings


def test_turn_completion_policy_allows_final_answer_when_all_plan_steps_are_done() -> None:
    policy = _policy()
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Find root cause",
            status="completed",
            steps=[
                AnalysisPlanStep(
                    id="baseline",
                    title="Build baseline",
                    status="done",
                    evidence_ids=["execution_result"],
                ),
                AnalysisPlanStep(
                    id="breakdown",
                    title="Breakdown",
                    status="done",
                    evidence_ids=["execution_result"],
                ),
            ],
            required_evidence=["execution_result"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ignored",
            finalizable_content="ignored",
        ),
        decision=FinalAnswerDecision(
            raw_final_text="Done with table evidence",
            finalization_mode="direct",
        ),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="prove with tables why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "decision_finalized"


def test_turn_completion_policy_auto_completes_present_results_step() -> None:
    captured: dict[str, Any] = {}

    def build_final_payload_from_parsed(**kwargs: Any) -> dict[str, Any]:
        state = kwargs["state"]
        captured["plan"] = state.analysis_plan.model_dump(mode="json")
        return {"answer": "built", "analysis_plan": captured["plan"]}

    policy = _policy(build_final_payload_from_parsed=build_final_payload_from_parsed)
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Present final result",
            status="running",
            steps=[
                AnalysisPlanStep(
                    id="present_results",
                    title="Present results",
                    status="running",
                    required_evidence=["final_answer"],
                )
            ],
            required_evidence=["final_answer"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ready",
            finalizable_content="ready",
        ),
        decision=FinalAnswerDecision(
            raw_final_text="Done",
            finalization_mode="direct",
        ),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="покажи результат"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **kwargs: build_final_payload_from_parsed(
            parsed_payload={"answer": kwargs["raw_final_text"]},
            fallback_answer=kwargs["raw_final_text"],
            state=kwargs["state"],
        ),
    )

    assert completion.completion_type == "decision_finalized"
    assert state.analysis_plan is not None
    assert state.analysis_plan.status == "completed"
    assert state.analysis_plan.steps[0].evidence_ids == ["final_answer"]
    assert completion.payload["analysis_plan"]["status"] == "completed"


def test_turn_completion_policy_blocks_structured_completion_without_plan_evidence() -> None:
    policy = _policy(
        validate_or_repair_final_payload=lambda **_: {
            "answer": "Done too early",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        }
    )
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Find root cause",
            status="running",
            steps=[AnalysisPlanStep(id="baseline", title="Build baseline")],
            required_evidence=["execution_result"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="Done too early",
            finalizable_content="Done too early",
            has_current_tools=True,
            current_tool_names=["run_sql"],
        ),
        decision=None,
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "analysis_plan_incomplete"
    assert "analysis_plan_missing_evidence:execution_result" in state.warnings


def test_turn_completion_policy_preserves_payload_on_analysis_plan_incomplete() -> None:
    captured: dict[str, Any] = {}

    def build_final_payload_from_parsed(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "answer": kwargs["parsed_payload"]["answer"],
            "visualization": kwargs["parsed_payload"]["visualization"],
        }

    policy = _policy(
        build_final_payload_from_parsed=build_final_payload_from_parsed,
        validate_or_repair_final_payload=lambda **_: {
            "answer": "Done too early",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
            "visualization": _VALID_VISUALIZATION,
        },
    )
    state = ToolOrchestrationState(
        analysis_plan=AnalysisPlan(
            plan_id="plan-1",
            goal="Find root cause",
            status="running",
            steps=[AnalysisPlanStep(id="baseline", title="Build baseline")],
            required_evidence=["execution_result"],
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="Done too early",
            finalizable_content="Done too early",
            has_current_tools=True,
            current_tool_names=["run_sql"],
        ),
        decision=None,
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "analysis_plan_incomplete"
    assert completion.payload["answer"] == "Done too early"
    assert completion.payload["visualization"] == _VALID_VISUALIZATION


def test_turn_completion_policy_blocks_table_proof_without_markdown_table() -> None:
    policy = _policy(
        is_acceptable_final_payload=lambda **kwargs: __import__(
            "rhetorica_backend.domain.chat.orchestration.final_payload_policy",
            fromlist=["FinalPayloadPolicy"],
        ).FinalPayloadPolicy.is_acceptable_final_payload(**kwargs),
        validate_or_repair_final_payload=lambda **_: {
            "answer": "Revenue dropped by 10.",
            "claims": [
                {"claim_id": "c1", "text": "Revenue dropped by 10.", "status": "verified"}
            ],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "type": "execution_result",
                    "reference": "exec-1",
                    "status": "verified",
                }
            ],
            "claim_evidence_links": [{"claim_id": "c1", "evidence_ids": ["e1"]}],
            "uncertainty_flags": [],
        },
    )
    from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
    from rhetorica_backend.domain.chat.orchestration.state import SqlExecutionAttempt

    state = ToolOrchestrationState()
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
        row_count=1,
        preview=QueryPreview(columns=["value"], rows=[[1]]),
    )
    state.execution = execution
    state.execution_attempts.append(
        SqlExecutionAttempt(
            sql="SELECT 1",
            connector_name="default",
            execution=execution,
        )
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="Revenue dropped by 10.",
            finalizable_content="Revenue dropped by 10.",
            has_current_tools=True,
            current_tool_names=["run_sql"],
        ),
        decision=None,
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="prove with tables why revenue dropped"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert "table_proof_required" in state.warnings


def test_turn_completion_policy_returns_incomplete_payload_for_abort() -> None:
    policy = _policy()
    state = ToolOrchestrationState()

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="ignored",
            finalizable_content="ignored",
        ),
        decision=AbortDecision(reason="protocol_repair_failed"),
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="hi"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "abort"
    assert completion.payload["answer"] == "incomplete"


def test_turn_completion_policy_applies_metadata_soft_fallback() -> None:
    state = ToolOrchestrationState()
    policy = _policy(
        is_acceptable_final_payload=lambda **_: False,
        validate_or_repair_final_payload=lambda **_: {
            "answer": "catalog result",
            "uncertainty_flags": [],
        },
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="catalog result",
            finalizable_content="catalog result",
            has_current_tools=True,
            current_tool_names=["search_metadata_catalog"],
        ),
        decision=None,
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="найди таблицу ft_order"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "fallback"
    assert completion.reason == "unacceptable_final_payload"
    assert completion.soft_fallback_applied is True
    assert completion.payload["warnings"] == [
        "deterministic_finalizer_used",
        "llm_unacceptable_final_payload",
    ]
    assert state.warnings == [
        "deterministic_finalizer_used",
        "llm_unacceptable_final_payload",
    ]


def test_turn_completion_policy_builds_successful_completion() -> None:
    state = ToolOrchestrationState()
    captured: dict[str, Any] = {}

    def build_final_payload_from_parsed(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "answer": "done",
            "warnings": list(kwargs["state"].warnings),
        }

    policy = _policy(
        build_final_payload_from_parsed=build_final_payload_from_parsed,
        has_needs_user_clarification_flag=lambda _: True,
        is_acceptable_final_payload=lambda **_: True,
        validate_or_repair_final_payload=lambda **_: {
            "answer": "done",
            "uncertainty_flags": ["needs_user_clarification"],
        },
    )

    completion = policy.complete_non_tool_turn(
        completion_input=NonToolTurnCompletionInput(
            raw_content="done",
            finalizable_content="done",
            has_current_tools=True,
            current_tool_names=["run_sql"],
        ),
        decision=None,
        llm_client=None,  # type: ignore[arg-type]
        request=ChatAskRequest(question="покажи выручку"),
        response_schema={},
        state=state,
        direct_plain_text_finalizer=lambda **_: {"answer": "direct"},
    )

    assert completion.completion_type == "success"
    assert completion.emit_visible_content is True
    assert completion.payload["answer"] == "done"
    assert "llm_user_clarification_requested" in state.warnings
    assert captured["fallback_answer"] == "done"


def test_turn_completion_policy_marks_partial_result_on_loop_limit() -> None:
    state = ToolOrchestrationState(generated_sql="SELECT 1")
    policy = _policy(
        incomplete_tool_orchestration_payload=lambda current_state: {
            "warnings": list(current_state.warnings),
        }
    )

    completion = policy.complete_loop_limit(state=state)

    assert completion.completion_type == "loop_limit"
    assert completion.payload["warnings"] == [
        "llm_tool_loop_limit_reached",
        "llm_tool_loop_partial_result_available",
    ]
