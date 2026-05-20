from __future__ import annotations

import json
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpointLifecycle,
    AnalysisPlan,
    AnalysisPlanCompletionGuard,
    AnalysisPlanLifecycle,
    AnalysisPlanStep,
    AnalysisRuntimeProgressService,
    AnalysisWorkerResult,
    AnalysisWorkerTask,
    LlmAnalysisPlanner,
    WorkerContextBudget,
)
from rhetorica_backend.domain.chat.orchestration.analysis_worker_runtime import (
    AnalysisWorkerRuntime,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class _WorkerToolExecutor:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def execute_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


class _RecordingWorkerToolExecutor:
    def execute_tool(self, **kwargs: Any) -> dict[str, Any]:
        state = kwargs["state"]
        execution = ExecuteSqlResponse(
            execution_id="exec-worker",
            status="succeeded",
            engine="clickhouse",
            normalized_sql="SELECT 1",
            row_count=1,
            preview=QueryPreview(columns=["value"], rows=[[1]]),
        )
        state.record_execution_outcome(
            sql="SELECT 1",
            connector_name="clickhouse-main",
            execution=execution,
        )
        return {
            "ok": True,
            "execution": execution.model_dump(mode="json"),
        }


class _PlanningCompletionResponse:
    def __init__(self, content: str) -> None:
        self.message = {"content": content}


class _PlanningLlmClient:
    def __init__(
        self,
        content: str,
        *,
        structured_disabled: bool = False,
    ) -> None:
        self.content = content
        self.structured_disabled = structured_disabled
        self.calls: list[dict[str, Any]] = []

    def chat_completion_with_fallback(self, **kwargs: Any):
        self.calls.append(kwargs)
        return (
            _PlanningCompletionResponse(self.content),
            False,
            self.structured_disabled,
        )


def _table_proof_plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-1",
        goal="Explain revenue drop",
        steps=[
            AnalysisPlanStep(
                id="baseline",
                title="Build SQL-backed baseline",
                required_evidence=["execution_result"],
            ),
            AnalysisPlanStep(
                id="breakdown",
                title="Break down drivers",
                required_evidence=["execution_result"],
            ),
            AnalysisPlanStep(
                id="evidence_check",
                title="Validate with table proof",
                required_evidence=["execution_result", "table_proof"],
            ),
        ],
        required_evidence=["execution_result", "table_proof"],
    )


def test_analysis_plan_lifecycle_tracks_root_cause_required_evidence() -> None:
    lifecycle = AnalysisPlanLifecycle()
    plan = lifecycle.create(
        plan_id="plan-1",
        goal="Find revenue drop root cause",
        steps=[
            AnalysisPlanStep(id="baseline", title="Build baseline"),
            AnalysisPlanStep(id="breakdown", title="Segment breakdown"),
            AnalysisPlanStep(id="evidence", title="Validate with SQL"),
        ],
        required_evidence=["exec-baseline", "exec-breakdown"],
    )

    plan = lifecycle.update_step(
        plan=plan,
        step_id="baseline",
        status="done",
        evidence_ids=["exec-baseline"],
    )
    plan = lifecycle.update_step(
        plan=plan,
        step_id="breakdown",
        status="done",
        evidence_ids=["exec-breakdown"],
    )
    plan = lifecycle.update_step(plan=plan, step_id="evidence", status="done")

    assert plan.status == "completed"
    assert AnalysisPlanCompletionGuard().can_finalize(plan) is True


def test_analysis_plan_guard_blocks_missing_required_evidence() -> None:
    lifecycle = AnalysisPlanLifecycle()
    plan = lifecycle.create(
        plan_id="plan-1",
        goal="Find revenue drop root cause",
        steps=[AnalysisPlanStep(id="baseline", title="Build baseline", status="done")],
        required_evidence=["exec-required"],
    )

    guard = AnalysisPlanCompletionGuard()

    assert guard.can_finalize(plan) is False
    assert guard.missing_evidence(plan) == ["exec-required"]


def test_analysis_plan_guard_blocks_planned_steps_even_when_evidence_exists() -> None:
    plan = AnalysisPlanLifecycle().create(
        plan_id="plan-1",
        goal="Find revenue drop root cause",
        steps=[
            AnalysisPlanStep(
                id="baseline",
                title="Build baseline",
                status="done",
                evidence_ids=["execution_result"],
            ),
            AnalysisPlanStep(id="breakdown", title="Segment breakdown"),
        ],
        required_evidence=["execution_result"],
    )

    guard = AnalysisPlanCompletionGuard()

    assert guard.can_finalize(plan) is False
    assert guard.incomplete_steps(plan) == ["breakdown"]


def test_llm_analysis_planner_creates_validated_plan() -> None:
    client = _PlanningLlmClient(
        json.dumps({
            "should_plan": True,
            "plan": {
                "goal": "Explain revenue drop",
                "steps": [
                    {
                        "id": "baseline",
                        "title": "Build SQL-backed baseline",
                        "required_evidence": ["execution_result"],
                    },
                    {
                        "id": "breakdown",
                        "title": "Break down drivers",
                        "required_evidence": ["execution_result", "table_proof"],
                    },
                ],
                "required_evidence": ["execution_result", "table_proof"],
                "open_questions": [],
            },
            "warnings": [],
        })
    )

    result = LlmAnalysisPlanner().plan(
        llm_client=client,
        question="Почему упала выручка? Докажи на таблицах",
        available_tools=["run_sql"],
    )

    assert result.plan is not None
    assert result.plan.required_evidence == ["execution_result", "table_proof"]
    assert [step.id for step in result.plan.steps] == [
        "baseline",
        "breakdown",
    ]
    assert client.calls[0]["response_schema"]["required"] == ["should_plan", "plan", "warnings"]


def test_llm_analysis_planner_accepts_explicit_reasoning_envelope() -> None:
    client = _PlanningLlmClient(
        "<think>internal reasoning</think>"
        + json.dumps({
            "should_plan": True,
            "plan": {
                "goal": "Explain revenue drop",
                "steps": [
                    {
                        "id": "metadata",
                        "title": "Find governed revenue tables",
                        "required_evidence": ["metadata_entity"],
                    },
                    {
                        "id": "proof",
                        "title": "Prove with SQL table",
                        "required_evidence": ["execution_result", "table_proof"],
                    },
                ],
                "required_evidence": ["table_proof"],
                "open_questions": [],
            },
            "warnings": [],
        })
    )

    result = LlmAnalysisPlanner().plan(
        llm_client=client,
        question="Докажи на таблицах",
        available_tools=["search_metadata_catalog", "run_sql"],
    )

    assert result.plan is not None
    assert "analysis_planner_reasoning_envelope_stripped" in result.warnings


def test_llm_analysis_planner_rejects_contract_violation() -> None:
    client = _PlanningLlmClient(
        json.dumps({
            "should_plan": True,
            "plan": {
                "goal": "Explain revenue drop",
                "steps": [
                    {
                        "id": "baseline",
                        "title": "Build baseline",
                        "required_evidence": ["execution_result"],
                    }
                ],
                "required_evidence": ["missing_evidence"],
                "open_questions": [],
            },
            "warnings": [],
        })
    )

    result = LlmAnalysisPlanner().plan(
        llm_client=client,
        question="Explain revenue drop",
        available_tools=["run_sql"],
    )

    assert result.plan is None
    assert "analysis_planner_contract_violation" in result.warnings


def test_analysis_runtime_progress_updates_plan_and_checkpoint_from_sql_result() -> None:
    state = ToolOrchestrationState()
    state.analysis_plan = _table_proof_plan()
    assert state.analysis_plan is not None
    state.checkpoint = AnalysisCheckpointLifecycle().start(
        run_id="run-1",
        plan_id=state.analysis_plan.plan_id,
    )
    service = AnalysisRuntimeProgressService()

    service.start_next_step(state=state, tool_name="run_sql")
    service.complete_current_step(
        state=state,
        tool_name="run_sql",
        tool_result={
            "ok": True,
            "execution": {
                "execution_id": "exec-1",
                "preview": {"columns": ["metric"], "rows": [[42]]},
            },
        },
    )

    assert state.analysis_plan.steps[0].status == "done"
    assert state.analysis_plan.steps[0].evidence_ids == [
        "execution_result",
        "exec-1",
        "table_proof",
    ]
    assert state.checkpoint is not None
    assert state.checkpoint.completed_step_ids == ["baseline"]
    assert "exec-1" in state.checkpoint.evidence_ids


def test_analysis_runtime_progress_updates_plan_from_metadata_result() -> None:
    state = ToolOrchestrationState()
    state.analysis_plan = AnalysisPlan(
        plan_id="plan-1",
        goal="Explain revenue drop",
        steps=[
            AnalysisPlanStep(
                id="metadata",
                title="Find governed revenue tables",
                required_evidence=["metadata_entity"],
            ),
            AnalysisPlanStep(
                id="proof",
                title="Prove with SQL",
                required_evidence=["execution_result", "table_proof"],
            ),
        ],
        required_evidence=["table_proof"],
    )
    assert state.analysis_plan is not None
    state.checkpoint = AnalysisCheckpointLifecycle().start(
        run_id="run-1",
        plan_id=state.analysis_plan.plan_id,
    )
    service = AnalysisRuntimeProgressService()

    service.start_next_step(state=state, tool_name="search_metadata_catalog")
    service.complete_current_step(
        state=state,
        tool_name="search_metadata_catalog",
        tool_result={"ok": True, "tables": [{"fqn": "dm.sales"}]},
    )

    assert [step.status for step in state.analysis_plan.steps] == [
        "done",
        "planned",
    ]
    assert state.analysis_plan.steps[0].evidence_ids == ["metadata_entity"]
    assert state.checkpoint.completed_step_ids == ["metadata"]


def test_analysis_runtime_progress_updates_plan_from_fetch_metadata_tables() -> None:
    state = ToolOrchestrationState()
    state.analysis_plan = AnalysisPlan(
        plan_id="plan-1",
        goal="Find table",
        steps=[
            AnalysisPlanStep(
                id="metadata",
                title="Find governed table",
                required_evidence=["metadata_entity"],
            )
        ],
        required_evidence=["metadata_entity"],
    )
    assert state.analysis_plan is not None
    state.checkpoint = AnalysisCheckpointLifecycle().start(
        run_id="run-1",
        plan_id=state.analysis_plan.plan_id,
    )
    service = AnalysisRuntimeProgressService()

    service.start_next_step(state=state, tool_name="fetch_metadata_tables")
    service.complete_current_step(
        state=state,
        tool_name="fetch_metadata_tables",
        tool_result={"ok": True, "tables": [{"fqn": "dm.sales"}]},
    )

    assert state.analysis_plan.steps[0].status == "done"
    assert state.analysis_plan.steps[0].evidence_ids == ["metadata_entity"]
    assert state.checkpoint.completed_step_ids == ["metadata"]


def test_analysis_planner_rejects_steps_without_required_evidence() -> None:
    client = _PlanningLlmClient(
        json.dumps({
            "should_plan": True,
            "plan": {
                "goal": "Show daily orders",
                "steps": [
                    {
                        "id": "present_results",
                        "title": "Present results",
                        "required_evidence": [],
                    },
                ],
                "required_evidence": [],
                "open_questions": [],
            },
            "warnings": [],
        })
    )

    result = LlmAnalysisPlanner().plan(
        llm_client=client,
        question="пришли заказы по дням",
        available_tools=["run_sql"],
    )

    assert result.plan is None
    assert "analysis_planner_invalid_response" in result.warnings


def test_analysis_worker_runtime_keeps_parent_prompt_compact() -> None:
    raw_payload = "RAW_WORKER_TRANSCRIPT:" + ("x" * 1_000)
    runtime = AnalysisWorkerRuntime(
        tool_executor=_WorkerToolExecutor({
            "ok": True,
            "execution": {
                "execution_id": "exec-worker",
                "status": "succeeded",
                "row_count": 1,
                "preview": {"columns": ["segment"], "rows": [["A"]]},
            },
            "raw_transcript": raw_payload,
        })
    )
    parent_state = ToolOrchestrationState(context_tables=["dm.sales"])
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="breakdown",
        goal="Check segment drivers",
        allowed_tools=["run_sql"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        request=ChatAskRequest(question="why revenue dropped"),
        db_connectors=[],
        parent_state=parent_state,
    )
    AnalysisWorkerRuntime.merge_result(parent_state=parent_state, result=result)
    artifact = runtime.prompt_artifact(result=result).stable_json()

    assert result.status == "succeeded"
    assert result.evidence_ids == ["exec-worker"]
    assert "columns=['segment']" in (result.key_finding or "")
    assert parent_state.worker_results == [result]
    assert "RAW_WORKER_TRANSCRIPT" not in artifact
    assert json.loads(artifact)["artifact_type"] == "worker_result"


def test_analysis_worker_runtime_fails_closed_on_tool_allowlist() -> None:
    runtime = AnalysisWorkerRuntime(tool_executor=_WorkerToolExecutor({"ok": True}))
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="baseline",
        goal="Build baseline",
        allowed_tools=["search_metadata_catalog"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        request=ChatAskRequest(question="why revenue dropped"),
        db_connectors=[],
        parent_state=ToolOrchestrationState(),
    )

    assert result.status == "failed"
    assert "worker_tool_not_allowed:run_sql" in (result.blocker or "")


def test_analysis_worker_runtime_enforces_allowed_sql_tables() -> None:
    runtime = AnalysisWorkerRuntime(tool_executor=_WorkerToolExecutor({"ok": True}))
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="baseline",
        goal="Build baseline",
        allowed_tools=["run_sql"],
        allowed_tables=["Sample-ClickHouse.default.dm.allowed_sales"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT count() FROM dm.other_sales"},
        request=ChatAskRequest(question="why revenue dropped"),
        db_connectors=[],
        parent_state=ToolOrchestrationState(),
    )

    assert result.status == "failed"
    assert result.blocker == "worker_table_not_allowed:dm.other_sales"


def test_analysis_worker_runtime_allows_scoped_sql_table_suffix() -> None:
    runtime = AnalysisWorkerRuntime(
        tool_executor=_WorkerToolExecutor({
            "ok": True,
            "execution": {
                "execution_id": "exec-worker",
                "status": "succeeded",
                "row_count": 1,
                "preview": {"columns": ["value"], "rows": [[1]]},
            },
        })
    )
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="baseline",
        goal="Build baseline",
        allowed_tools=["run_sql"],
        allowed_tables=["Sample-ClickHouse.default.dm.allowed_sales"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT count() FROM dm.allowed_sales"},
        request=ChatAskRequest(question="why revenue dropped"),
        db_connectors=[],
        parent_state=ToolOrchestrationState(),
    )

    assert result.status == "succeeded"


def test_analysis_worker_runtime_allows_four_part_scoped_sql_table() -> None:
    runtime = AnalysisWorkerRuntime(
        tool_executor=_WorkerToolExecutor({
            "ok": True,
            "execution": {
                "execution_id": "exec-worker",
                "status": "succeeded",
                "row_count": 1,
                "preview": {"columns": ["value"], "rows": [[1]]},
            },
        })
    )
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="baseline",
        goal="Build baseline",
        allowed_tools=["run_sql"],
        allowed_tables=["trino_omni.iceberg.dm_ft.ft_order_art_sber"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={
            "sql": "SELECT * FROM trino_omni.iceberg.dm_ft.ft_order_art_sber LIMIT 5"
        },
        request=ChatAskRequest(question="sample rows"),
        db_connectors=[],
        parent_state=ToolOrchestrationState(),
    )

    assert result.status == "succeeded"
    assert result.evidence_ids == ["exec-worker"]


def test_analysis_worker_runtime_enforces_result_row_budget() -> None:
    runtime = AnalysisWorkerRuntime(
        tool_executor=_WorkerToolExecutor({
            "ok": True,
            "execution": {
                "execution_id": "exec-worker",
                "status": "succeeded",
                "row_count": 3,
                "preview": {"columns": ["value"], "rows": [[1], [2], [3]]},
            },
        })
    )
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="baseline",
        goal="Build baseline",
        allowed_tools=["run_sql"],
        budget=WorkerContextBudget(max_rows=1),
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        request=ChatAskRequest(question="why revenue dropped"),
        db_connectors=[],
        parent_state=ToolOrchestrationState(),
    )

    assert result.status == "blocked"
    assert result.blocker == "worker_row_budget_exceeded:3>1"


def test_analysis_worker_runtime_executes_independent_tasks_and_merges_compactly() -> None:
    runtime = AnalysisWorkerRuntime(
        tool_executor=_WorkerToolExecutor({
            "ok": True,
            "execution": {
                "execution_id": "exec-worker",
                "status": "succeeded",
                "row_count": 1,
                "preview": {"columns": ["value"], "rows": [[1]]},
            },
        })
    )
    parent_state = ToolOrchestrationState()
    tasks = [
        (
            AnalysisWorkerTask(
                task_id=f"worker-{index}",
                plan_id="plan-1",
                step_id=f"step-{index}",
                goal=f"Check branch {index}",
                allowed_tools=["run_sql"],
            ),
            "run_sql",
            {"sql": "SELECT 1"},
        )
        for index in range(2)
    ]

    results = runtime.execute_independent_tool_tasks(
        tasks=tasks,
        request=ChatAskRequest(question="root cause"),
        db_connectors=[],
        parent_state=parent_state,
    )
    for result in results:
        AnalysisWorkerRuntime.merge_result(parent_state=parent_state, result=result)

    assert [result.status for result in results] == ["succeeded", "succeeded"]
    assert [result.task_id for result in parent_state.worker_results] == [
        "worker-0",
        "worker-1",
    ]


def test_analysis_worker_runtime_merges_sql_execution_evidence_into_parent() -> None:
    runtime = AnalysisWorkerRuntime(tool_executor=_RecordingWorkerToolExecutor())
    parent_state = ToolOrchestrationState()
    task = AnalysisWorkerTask(
        task_id="worker-1",
        plan_id="plan-1",
        step_id="breakdown",
        goal="Check branch",
        allowed_tools=["run_sql"],
    )

    result = runtime.execute_single_tool_task(
        task=task,
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        request=ChatAskRequest(question="root cause"),
        db_connectors=[],
        parent_state=parent_state,
    )
    AnalysisWorkerRuntime.merge_result(parent_state=parent_state, result=result)

    assert result.execution_outcome is not None
    assert parent_state.execution is not None
    assert parent_state.execution.execution_id == "exec-worker"
    assert parent_state.execution_attempts[0].execution.preview.rows == [[1]]


def test_checkpoint_lifecycle_resume_cancel_digest_has_no_transcript() -> None:
    lifecycle = AnalysisCheckpointLifecycle()
    checkpoint = lifecycle.start(run_id="run-1", plan_id="plan-1")
    checkpoint = lifecycle.mark_step_running(
        checkpoint=checkpoint,
        step_id="baseline",
    )
    checkpoint = lifecycle.mark_step_completed(
        checkpoint=checkpoint,
        step_id="baseline",
        evidence_ids=["exec-1"],
    )
    cancelled = lifecycle.cancel(checkpoint=checkpoint)
    digest = cancelled.digest_payload()

    assert digest["status"] == "cancelled"
    assert digest["completed_step_ids"] == ["baseline"]
    assert digest["evidence_ids"] == ["exec-1"]
    assert "messages" not in digest
    assert "raw_transcript" not in json.dumps(digest)


def test_checkpoint_lifecycle_tracks_pending_and_approved_sql_gate() -> None:
    lifecycle = AnalysisCheckpointLifecycle()
    checkpoint = lifecycle.start(run_id="run-1", plan_id="plan-1")
    checkpoint = lifecycle.mark_step_running(
        checkpoint=checkpoint,
        step_id="baseline",
    )

    gated = lifecycle.require_approval(
        checkpoint=checkpoint,
        step_id="baseline",
        blocker="sql_approval_required",
        approval={
            "kind": "sql_execution_approval",
            "approval_token": "tok-1",
            "sql": "SELECT 1",
        },
    )
    approved = lifecycle.approve(checkpoint=gated)

    assert gated.status == "awaiting_user"
    assert gated.blocked_step_id == "baseline"
    assert gated.budget_state["pending_approval"]["approval_token"] == "tok-1"
    assert approved.status == "running"
    assert approved.active_step_id == "baseline"
    assert approved.budget_state["approved_approval_token"] == "tok-1"
    assert "pending_approval" not in approved.budget_state


def test_analysis_worker_compact_payload_includes_ok_for_succeeded_status() -> None:
    """compact_payload() must include 'ok: True' when status is 'succeeded'."""
    result = AnalysisWorkerResult(
        task_id="worker-1",
        goal="Check revenue",
        status="succeeded",
        evidence_ids=["exec-1"],
    )
    payload = result.compact_payload()

    assert payload["ok"] is True
    assert payload["status"] == "succeeded"
    assert payload["task_id"] == "worker-1"


def test_analysis_worker_compact_payload_includes_ok_false_for_failed_status() -> None:
    """compact_payload() must include 'ok: False' when status is 'failed'."""
    result = AnalysisWorkerResult(
        task_id="worker-1",
        goal="Check revenue",
        status="failed",
        blocker="connection_timeout",
    )
    payload = result.compact_payload()

    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["blocker"] == "connection_timeout"


def test_analysis_worker_compact_payload_includes_ok_false_for_blocked_status() -> None:
    """compact_payload() must include 'ok: False' when status is 'blocked'."""
    result = AnalysisWorkerResult(
        task_id="worker-1",
        goal="Check revenue",
        status="blocked",
        blocker="worker_row_budget_exceeded",
    )
    payload = result.compact_payload()

    assert payload["ok"] is False
    assert payload["status"] == "blocked"


def test_analysis_worker_compact_payload_includes_ok_false_for_cancelled_status() -> None:
    """compact_payload() must include 'ok: False' when status is 'cancelled'."""
    result = AnalysisWorkerResult(
        task_id="worker-1",
        goal="Check revenue",
        status="cancelled",
    )
    payload = result.compact_payload()

    assert payload["ok"] is False
    assert payload["status"] == "cancelled"
