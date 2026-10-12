"""Recoverable coding lifecycle with all external effects behind one port."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.coding import (
    AgentTask,
    CandidateManifest,
    CodingCoordinatorDecision,
    CodingPartialResult,
    CodingReasonCode,
    CodingRun,
    CodingRunAdmission,
    CodingRunPhase,
    CodingRunStatus,
    CodingTeamMemberCapability,
    PublicationReceipt,
    QaVerdict,
    ServerValidationEvidence,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)
from rhetorica_backend.domain.coding.deadlines import (
    CodingDeadlineCoordinator,
    CodingOperationDeadline,
)
from rhetorica_backend.domain.coding.errors import (
    CodingBudgetExhausted,
    CodingRunConflict,
    CodingRunError,
)
from rhetorica_backend.domain.coding.publication_content_policy import PublicationDenied
from rhetorica_backend.domain.coding.publication_decision import CodingPublicationDecisionService
from rhetorica_backend.domain.coding.teams import run_team_snapshot
from rhetorica_backend.domain.coding.validation import ValidationReportAssembler
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository


@dataclass(frozen=True)
class DeveloperExecution:
    candidate: CandidateManifest
    checkpoint_ref: str
    artifact_ids: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class QaExecution:
    verdict: QaVerdict
    evidence: tuple[ServerValidationEvidence, ...]
    checkpoint_ref: str
    artifact_ids: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class CoordinatorExecution:
    decision: CodingCoordinatorDecision


MemberInvoker = Callable[[str, str, str], Awaitable[dict[str, Any]]]


class CodingLifecycleExecutor:
    """I/O implementation supplied by the coding runtime composition root.

    It is intentionally narrower than a generic agent executor: it receives a
    server-owned principal/task, a stable model reservation and a deadline. A
    concrete adapter can use the existing Agents SDK, sandbox, DWH and G
    publisher, but it cannot receive arbitrary connector configuration from a
    chat message or model tool call.
    """

    async def prepare(self, *, admission: CodingRunAdmission) -> None:
        raise NotImplementedError

    async def develop(
        self,
        *,
        admission: CodingRunAdmission,
        task: AgentTask,
        operation_id: str,
        deadline: CodingOperationDeadline,
        assignment: str = "",
    ) -> DeveloperExecution:
        """Candidate-capable work in the task's writable sandbox."""

        raise NotImplementedError

    async def qa(
        self,
        *,
        admission: CodingRunAdmission,
        task: AgentTask,
        candidate: CandidateManifest,
        operation_id: str,
        deadline: CodingOperationDeadline,
        assignment: str = "",
    ) -> QaExecution:
        """Independent review of an immutable candidate in a separate sandbox."""

        raise NotImplementedError

    async def coordinate(
        self,
        *,
        admission: CodingRunAdmission,
        task: AgentTask,
        operation_id: str,
        deadline: CodingOperationDeadline,
        invoke_member: MemberInvoker,
    ) -> CoordinatorExecution:
        """Run the SDK coordinator with only the admitted members as tools."""

        raise NotImplementedError

    async def publish(
        self,
        *,
        admission: CodingRunAdmission,
        candidate: CandidateManifest,
        report: ValidationReport,
    ) -> PublicationReceipt:
        raise NotImplementedError

    async def cancel(self, *, admission: CodingRunAdmission) -> tuple[str, ...]:
        """Best-effort remote cancellation; returns retained artifact IDs only."""

        raise NotImplementedError


REVIEW_CHECK_PREFIX = "independent_review:"
REVIEW_REPORT_LIMIT = 8_000


class CodingLifecycleService:
    """Persist each boundary so Temporal may safely retry the next activity."""

    def __init__(
        self,
        *,
        runs: CodingRunRepository,
        executor: CodingLifecycleExecutor,
        deadlines: CodingDeadlineCoordinator | None = None,
        reports: ValidationReportAssembler | None = None,
        publications: CodingPublicationDecisionService | None = None,
    ) -> None:
        self._runs = runs
        self._executor = executor
        self._deadlines = deadlines or CodingDeadlineCoordinator(runs)
        self._reports = reports or ValidationReportAssembler(runs)
        self._publications = publications or CodingPublicationDecisionService()

    async def prepare(self, *, run_id: str) -> CodingRun:
        admission = self._admission(run_id)
        run = admission.run
        if run.phase != CodingRunPhase.PREPARING:
            return run
        await self._executor.prepare(admission=admission)
        return self._runs.transition_run(
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            phase=CodingRunPhase.DEVELOPING,
            status=CodingRunStatus.RUNNING,
            event_type="run.prepared",
        )

    async def develop(self, *, run_id: str, cycle: int) -> CandidateManifest:
        """Legacy fixed-workflow step kept for runs started before coding teams."""

        admission = self._admission(run_id)
        task = self._task_for_role(admission.run, "developer")
        candidate, _ = await self._candidate_work(admission, task, f"developer:{cycle}")
        return candidate

    async def qa(self, *, run_id: str, cycle: int) -> ValidationReport:
        """Legacy fixed-workflow step kept for runs started before coding teams."""

        admission = self._admission(run_id)
        candidate = admission.run.candidate
        if candidate is None:
            raise CodingRunConflict("coding_qa_candidate_missing")
        task = self._task_for_role(admission.run, "qa")
        existing = self._existing_report(admission.run, candidate)
        if existing is not None:
            return existing
        report, _, _ = await self._review_work(admission, task, candidate, f"qa:{cycle}")
        return report

    async def coordinate(self, *, run_id: str, turn: int) -> CodingCoordinatorDecision:
        """One durable coordinator turn; the SDK, not the server, routes members."""

        admission = self._admission(run_id)
        run = admission.run
        task = self._task_for_role(run, "coordinator")
        operation_id = f"coordinator:{turn}"
        deadline = self._deadlines.operation_deadline(
            run=run, task=task, environment=admission.environment_profile
        )
        if task.status != CodingRunStatus.RUNNING:
            task = self._checkpoint_task(
                task,
                status=CodingRunStatus.RUNNING,
                attempts=task.attempts + 1,
                event_type="team.coordinator_started",
            )

        async def invoke(member_id: str, call_id: str, assignment: str) -> dict[str, Any]:
            return await self.invoke_member(
                run_id=run_id, member_id=member_id, call_id=call_id, assignment=assignment
            )

        outcome = await self._executor.coordinate(
            admission=admission,
            task=task,
            operation_id=operation_id,
            deadline=deadline,
            invoke_member=invoke,
        )
        task = self._current_task(task)
        self._checkpoint_task(
            task,
            status=CodingRunStatus.SUCCEEDED,
            event_type="team.coordinator_completed",
        )
        return outcome.decision

    async def invoke_member(
        self, *, run_id: str, member_id: str, call_id: str, assignment: str
    ) -> dict[str, Any]:
        """Execute one coordinator assignment inside the member's own boundary.

        The returned dictionary is the member tool output seen by the coordinator.
        Recoverable outcomes, including a failed review, are results; budget,
        deadline and authorization failures stay exceptions and end the run.
        """

        admission = self._admission(run_id)
        run = admission.run
        member = run_team_snapshot(run).member(member_id)
        if member is None:
            raise CodingRunConflict("coding_team_member_unknown")
        task = self._member_task(run, member_id, member.storage_role)
        # Fixed length: the operation ID is part of a 128-character reservation key.
        call_digest = hashlib.sha256(f"{member_id}:{call_id}".encode()).hexdigest()[:32]
        operation_id = f"member:{call_digest}"
        if member.capability == CodingTeamMemberCapability.CANDIDATE:
            produced, summary = await self._candidate_work(
                admission, task, operation_id, assignment=assignment
            )
            return {
                "status": "candidate_recorded",
                "member": member.label,
                "candidate_digest": produced.digest,
                "changed_paths": produced.changed_paths,
                "summary": summary[:4000],
            }
        candidate = run.candidate
        if candidate is None:
            return {
                "status": "blocked",
                "member": member.label,
                "reason": "No candidate exists yet; ask a candidate-capable member first.",
            }
        previous = self._member_review(run, candidate, member.id)
        if previous is not None:
            # The recorded verdict is binding; no second model run for the same candidate.
            return {
                "status": previous.status,
                "member": member.label,
                "candidate_digest": candidate.digest,
                "reason": previous.reason or "",
                "repeat": True,
                "note": "This member already reviewed this immutable candidate; "
                "a new candidate is needed for a new verdict.",
            }
        report, verdict, summary = await self._review_work(
            admission, task, candidate, operation_id, assignment=assignment, member_id=member.id
        )
        return {
            "status": verdict.status,
            "member": member.label,
            "candidate_digest": candidate.digest,
            "reason": (summary or verdict.reason or "")[:REVIEW_REPORT_LIMIT],
            "report_id": report.id,
        }

    async def finish_turn(
        self, *, run_id: str, decision: CodingCoordinatorDecision
    ) -> tuple[CodingRun, bool]:
        """Persist the coordinator's request; return whether publication may be attempted."""

        admission = self._admission(run_id)
        run = admission.run
        if (
            decision.decision == "publish"
            and run.candidate is not None
            and run.validation_report_id
        ):
            return run, True
        reason = (
            CodingReasonCode.POLICY_DENIED
            if decision.decision == "publish"
            else CodingReasonCode.COORDINATOR_STOPPED
        )
        artifact_ids = await self._cancel_artifacts(admission)
        stopped = self._deadlines.cancel_or_expire(
            run=run,
            partial_result=CodingPartialResult(
                artifact_ids=list(artifact_ids),
                summary=decision.summary,
                stopped_reason=reason,
            ),
            reason_code=reason,
            cancelled=False,
        )
        return stopped, False

    async def stop_after_policy_denial(self, *, run_id: str, error: CodingRunError) -> CodingRun:
        """A live grant revoked during the run ends it without publication."""

        admission = self._admission(run_id)
        reason = (
            CodingReasonCode.RESOURCE_ACCESS_REVOKED
            if error.code in {"resource_access_revoked", "coding_agent_unavailable"}
            else CodingReasonCode.POLICY_DENIED
        )
        artifact_ids = await self._cancel_artifacts(admission)
        return self._deadlines.cancel_or_expire(
            run=admission.run,
            partial_result=CodingPartialResult(
                artifact_ids=list(artifact_ids),
                summary=f"Coding run stopped after an authorization check: {error.code}.",
                stopped_reason=reason,
            ),
            reason_code=reason,
            cancelled=False,
        )

    async def _candidate_work(
        self,
        admission: CodingRunAdmission,
        task: AgentTask,
        operation_id: str,
        *,
        assignment: str = "",
    ) -> tuple[CandidateManifest, str]:
        run = admission.run
        checkpoint_prefix = f"{operation_id}:"
        if (
            task.status == CodingRunStatus.SUCCEEDED
            and task.checkpoint_ref
            and task.checkpoint_ref.startswith(checkpoint_prefix)
        ):
            digest = task.checkpoint_ref.removeprefix(checkpoint_prefix)
            for candidate in self._runs.candidates(
                run_id=run.id,
                workspace_id=run.workspace_id,
                owner_user_id=run.owner_user_id,
            ):
                if candidate.digest == digest:
                    return candidate, ""
            raise CodingRunConflict("coding_developer_checkpoint_candidate_missing")
        reservation = self._runs.reserve_iteration(
            run_id=run.id,
            task_id=task.id,
            executing_agent_profile_id=task.agent_profile_id,
            invocation_id=f"{run.id}:{task.id}:{operation_id}",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        deadline = self._deadlines.operation_deadline(
            run=run,
            task=task,
            environment=admission.environment_profile,
            budget_deadline=reservation.deadline,
        )
        task = self._current_task(task)
        task = self._checkpoint_task(
            task,
            status=CodingRunStatus.RUNNING,
            attempts=task.attempts + 1,
            event_type="task.developer_started",
        )
        # Passed only when present so executors without the parameter keep working.
        extra = {"assignment": assignment} if assignment else {}
        outcome = await self._executor.develop(
            admission=admission,
            task=task,
            operation_id=operation_id,
            deadline=deadline,
            **extra,
        )
        self._require_candidate_scope(run, outcome.candidate)
        self._runs.record_candidate(outcome.candidate)
        task = self._current_task(task)
        self._checkpoint_task(
            task,
            status=CodingRunStatus.SUCCEEDED,
            checkpoint_ref=f"{checkpoint_prefix}{outcome.candidate.digest}",
            result_artifact_ids=_merge_refs(task.result_artifact_ids, outcome.artifact_ids),
            event_type="task.developer_completed",
        )
        return outcome.candidate, outcome.summary

    async def _review_work(
        self,
        admission: CodingRunAdmission,
        task: AgentTask,
        candidate: CandidateManifest,
        operation_id: str,
        *,
        assignment: str = "",
        member_id: str | None = None,
    ) -> tuple[ValidationReport, QaVerdict, str]:
        run = admission.run
        reservation = self._runs.reserve_iteration(
            run_id=run.id,
            task_id=task.id,
            executing_agent_profile_id=task.agent_profile_id,
            invocation_id=f"{run.id}:{task.id}:{operation_id}",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        deadline = self._deadlines.operation_deadline(
            run=run,
            task=task,
            environment=admission.environment_profile,
            budget_deadline=reservation.deadline,
        )
        task = self._current_task(task)
        task = self._checkpoint_task(
            task,
            status=CodingRunStatus.RUNNING,
            attempts=task.attempts + 1,
            event_type="task.qa_started",
        )
        extra = {"assignment": assignment} if assignment else {}
        outcome = await self._executor.qa(
            admission=admission,
            task=task,
            candidate=candidate,
            operation_id=operation_id,
            deadline=deadline,
            **extra,
        )
        if (
            outcome.verdict.task_id != task.id
            or outcome.verdict.qa_agent_id != task.agent_profile_id
            or outcome.verdict.candidate_digest != candidate.digest
        ):
            raise CodingRunConflict("coding_qa_verdict_task_mismatch")
        for evidence in outcome.evidence:
            self._runs.record_validation_evidence(evidence)
        if member_id is not None:
            # Each reviewer is a mandatory check, so a later pass cannot hide a failure.
            self._runs.record_validation_evidence(
                ServerValidationEvidence(
                    id=str(uuid5(NAMESPACE_URL, f"rhetorica/coding/review/{outcome.verdict.id}")),
                    workspace_id=run.workspace_id,
                    owner_user_id=run.owner_user_id,
                    run_id=run.id,
                    task_id=task.id,
                    candidate_digest=candidate.digest,
                    name=f"{REVIEW_CHECK_PREFIX}{member_id}",
                    source="system",
                    status=ValidationStatus(outcome.verdict.status),
                    mandatory=True,
                    duration_ms=0,
                    reason=outcome.verdict.reason,
                    created_at=outcome.verdict.created_at,
                )
            )
        report = self._reports.assemble(
            candidate=candidate, qa_verdict=outcome.verdict, team=run.team_snapshot
        )
        report = self._runs.save_validation_report(report=report, owner_user_id=run.owner_user_id)
        task = self._current_task(task)
        self._checkpoint_task(
            task,
            status=CodingRunStatus.SUCCEEDED,
            checkpoint_ref=f"{operation_id}:{report.id}",
            result_artifact_ids=_merge_refs(task.result_artifact_ids, outcome.artifact_ids),
            event_type="task.qa_completed",
        )
        return report, outcome.verdict, outcome.summary

    def _member_review(
        self, run: CodingRun, candidate: CandidateManifest, member_id: str
    ) -> ValidationCheck | None:
        """Return this reviewer's reported verdict on the immutable candidate."""

        report = self._existing_report(run, candidate)
        if report is None:
            # Evidence without a saved report means the review was interrupted.
            return None
        name = f"{REVIEW_CHECK_PREFIX}{member_id}"
        return next(
            (
                check
                for check in report.checks
                if check.name == name and check.receipt_id is not None
            ),
            None,
        )

    def _member_task(self, run: CodingRun, member_id: str, role: str) -> AgentTask:
        tasks = self._runs.tasks(
            run_id=run.id, workspace_id=run.workspace_id, owner_user_id=run.owner_user_id
        )
        matches = [task for task in tasks if task.team_member_id == member_id]
        if not matches and run.team_snapshot is None:
            # Runs admitted before coding teams carry exactly one task per role.
            matches = [task for task in tasks if task.role == role]
        if len(matches) != 1 or matches[0].role != role:
            raise CodingRunConflict("coding_team_member_task_required")
        return matches[0]

    async def publish(self, *, run_id: str) -> CodingRun:
        admission = self._admission(run_id)
        run = admission.run
        candidate = run.candidate
        if candidate is None or run.validation_report_id is None:
            raise CodingRunConflict("coding_publication_prerequisites_missing")
        report = self._runs.validation_report(
            report_id=run.validation_report_id,
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        decision = self._publications.decide(run=run, report=report)
        if not decision.allowed:
            await self._cancel_artifacts(admission)
            return self._runs.transition_run(
                run_id=run.id,
                workspace_id=run.workspace_id,
                owner_user_id=run.owner_user_id,
                phase=CodingRunPhase.COMPLETED,
                status=CodingRunStatus.FAILED,
                reason_code=(decision.reason_code or CodingReasonCode.POLICY_DENIED).value,
                event_type="publication.blocked",
                payload={"missing_required_reviews": list(decision.missing_reviews)},
            )
        try:
            receipt = await self._executor.publish(
                admission=admission,
                candidate=candidate,
                report=report,
            )
        except PublicationDenied as exc:
            if exc.reason_code == CodingReasonCode.PUBLICATION_NOT_AUTHORIZED:
                return self._runs.transition_run(
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    owner_user_id=run.owner_user_id,
                    phase=CodingRunPhase.COMPLETED,
                    status=CodingRunStatus.SUCCEEDED,
                    reason_code=exc.reason_code.value,
                    event_type="publication.not_authorized",
                    payload={"limitations": list(decision.limitation_names)},
                )
            raise
        return self._runs.record_publication_receipt(receipt)

    async def cancel(self, *, run_id: str) -> CodingRun:
        admission = self._admission(run_id)
        artifact_ids = await self._cancel_artifacts(admission)
        return self._deadlines.cancel_or_expire(
            run=admission.run,
            partial_result=CodingPartialResult(
                artifact_ids=list(artifact_ids),
                summary="Coding run cancelled before publication.",
                stopped_reason=CodingReasonCode.CANCELLED_BY_OWNER,
            ),
            reason_code=CodingReasonCode.CANCELLED_BY_OWNER,
            cancelled=True,
        )

    async def stop_after_budget_or_deadline(
        self, *, run_id: str, error: CodingBudgetExhausted
    ) -> CodingRun:
        admission = self._admission(run_id)
        reason = (
            CodingReasonCode.RUNTIME_LIMIT_REACHED
            if error.code == "coding_runtime_limit_reached"
            else CodingReasonCode.ITERATION_LIMIT_REACHED
        )
        artifact_ids = await self._cancel_artifacts(admission)
        return self._deadlines.cancel_or_expire(
            run=admission.run,
            partial_result=CodingPartialResult(
                artifact_ids=list(artifact_ids),
                summary="Coding run stopped before an unreserved model invocation.",
                stopped_reason=reason,
            ),
            reason_code=reason,
            cancelled=False,
        )

    async def fail_after_activity_error(
        self, *, run_id: str, reason_code: CodingReasonCode = CodingReasonCode.EXECUTION_FAILED
    ) -> CodingRun:
        """Persist a safe terminal result after Temporal exhausts an activity.

        The workflow deliberately passes no exception text: exception bodies may
        contain provider or sandbox details and must not enter Temporal history.
        """

        if reason_code not in {
            CodingReasonCode.EXECUTION_FAILED,
            CodingReasonCode.DEPENDENCY_INSTALL_FAILED,
        }:
            raise CodingRunConflict("coding_failure_reason_invalid")
        admission = self._admission(run_id)
        artifact_ids = await self._cancel_artifacts(admission)
        return self._deadlines.cancel_or_expire(
            run=admission.run,
            partial_result=CodingPartialResult(
                artifact_ids=list(artifact_ids),
                summary="Coding workflow stopped after an activity error.",
                stopped_reason=reason_code,
            ),
            reason_code=reason_code,
            cancelled=False,
        )

    def _admission(self, run_id: str) -> CodingRunAdmission:
        # A run ID alone is safe in Temporal history. Scope is recovered from the
        # server-owned admission snapshot and never from activity input.
        return self._runs.get_admission_for_worker(run_id=run_id)

    async def _cancel_artifacts(self, admission: CodingRunAdmission) -> tuple[str, ...]:
        """A failed remote cancellation must not prevent durable finalization."""

        try:
            return await self._executor.cancel(admission=admission)
        except Exception:
            # Finalization must survive the same connector/sandbox defect that
            # caused the activity to fail. Cancellation evidence is optional;
            # the terminal state is not.
            return ()

    def _task_for_role(self, run: CodingRun, role: str) -> AgentTask:
        tasks = [
            task
            for task in self._runs.tasks(
                run_id=run.id,
                workspace_id=run.workspace_id,
                owner_user_id=run.owner_user_id,
            )
            if task.role == role
        ]
        if len(tasks) != 1:
            raise CodingRunConflict(f"coding_{role}_task_required")
        return tasks[0]

    def _current_task(self, task: AgentTask) -> AgentTask:
        """Reload a task after an executor may persist skill-read addresses."""

        return self._runs.get_task(
            task_id=task.id,
            workspace_id=task.workspace_id,
            owner_user_id=task.owner_user_id,
        )

    def _existing_report(
        self, run: CodingRun, candidate: CandidateManifest
    ) -> ValidationReport | None:
        if run.validation_report_id is None:
            return None
        report = self._runs.validation_report(
            report_id=run.validation_report_id,
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        return report if report.candidate_digest == candidate.digest else None

    def _checkpoint_task(
        self,
        task: AgentTask,
        *,
        status: CodingRunStatus,
        iterations_used: int | None = None,
        attempts: int | None = None,
        checkpoint_ref: str | None = None,
        result_artifact_ids: list[str] | None = None,
        event_type: str,
    ) -> AgentTask:
        return self._runs.checkpoint_task(
            task.model_copy(
                update={
                    "status": status,
                    "iterations_used": task.iterations_used
                    if iterations_used is None
                    else iterations_used,
                    "attempts": task.attempts if attempts is None else attempts,
                    "checkpoint_ref": checkpoint_ref
                    if checkpoint_ref is not None
                    else task.checkpoint_ref,
                    "result_artifact_ids": (
                        task.result_artifact_ids
                        if result_artifact_ids is None
                        else result_artifact_ids
                    ),
                }
            ),
            expected_version=task.version,
            event_type=event_type,
        )

    @staticmethod
    def _require_candidate_scope(run: CodingRun, candidate: CandidateManifest) -> None:
        if (
            candidate.run_id != run.id
            or candidate.workspace_id != run.workspace_id
            or candidate.owner_user_id != run.owner_user_id
            or candidate.base_sha != run.repository.base_sha
        ):
            raise CodingRunConflict("coding_candidate_scope_mismatch")


def _merge_refs(existing: list[str], added: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys([*existing, *added]))
