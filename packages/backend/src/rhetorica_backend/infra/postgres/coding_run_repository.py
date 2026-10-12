"""PostgreSQL control-plane repository for durable governed coding work."""

from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    AgentTask,
    CandidateManifest,
    CodingBudgetLedger,
    CodingIterationReservation,
    CodingPartialResult,
    CodingRun,
    CodingRunAdmission,
    CodingRunAdmissionResult,
    CodingRunEvent,
    CodingRunOutboxEntry,
    CodingRunPhase,
    CodingRunStatus,
    PublicationReceipt,
    ServerValidationEvidence,
    ValidationReport,
    ValidationStatus,
)
from rhetorica_backend.domain.coding.errors import (
    CodingBudgetExhausted,
    CodingRunConflict,
    CodingRunNotFound,
    CodingTaskConflict,
)
from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm
from rhetorica_backend.infra.postgres.coding_run_models import (
    CodingAgentTaskOrm,
    CodingBudgetLedgerOrm,
    CodingCandidateOrm,
    CodingIterationReservationOrm,
    CodingRunEventOrm,
    CodingRunOrm,
    CodingRunOutboxOrm,
    CodingValidationEvidenceOrm,
    CodingValidationReportOrm,
    utcnow,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class CodingRunRepository:
    """Persist only server-owned run state and compact, scope-bound events.

    The repository deliberately accepts a fully validated ``CodingRunAdmission``.
    Authentication and policy selection remain at the use-case boundary; this
    adapter prevents a caller from later escaping the captured workspace/owner
    scope or modifying an immutable task skill binding.
    """

    _sqlite_write_lock = threading.Lock()

    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    @staticmethod
    def workflow_id(run_id: str) -> str:
        return f"coding/{run_id}"

    def admit(
        self, admission: CodingRunAdmission, *, tasks: list[AgentTask] | None = None
    ) -> CodingRunAdmissionResult:
        """Atomically store a run, its initial event and one start command.

        The unique idempotency key serializes concurrent browser retries. A
        repeated request with identical input returns its original run; changing
        the input behind the same key is an explicit conflict.
        """

        run = admission.run
        with self._write_session() as session, session.no_autoflush:
            row = self._run_orm(admission)
            session.add(row)
            try:
                session.flush([row])
            except IntegrityError:
                session.rollback()
                return self._duplicate_admission(admission)
            self._append_event(session, row, "run.queued")
            initial_tasks = tasks or []
            task_ids = {task.id for task in initial_tasks}
            for task in initial_tasks:
                if (
                    task.run_id != run.id
                    or task.workspace_id != run.workspace_id
                    or task.owner_user_id != run.owner_user_id
                    or (task.parent_task_id is not None and task.parent_task_id not in task_ids)
                ):
                    raise CodingRunConflict("coding_initial_task_scope_mismatch")
                session.add(self._task_orm(task))
            session.add(
                CodingRunOutboxOrm(
                    id=f"start:{run.id}",
                    workspace_id=run.workspace_id,
                    owner_user_id=run.owner_user_id,
                    run_id=run.id,
                    kind="start",
                    payload={},
                    delivered=False,
                    attempts=0,
                    created_at=utcnow(),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return self._duplicate_admission(admission)
            return CodingRunAdmissionResult(
                run=self._run_contract(row), created=True, workflow_id=self.workflow_id(row.id)
            )

    def get(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> CodingRun:
        with self._db.session() as session:
            return self._run_contract(self._owned_run(session, run_id, workspace_id, owner_user_id))

    def list_owned(
        self, *, workspace_id: str, owner_user_id: str, conversation_id: str | None = None
    ) -> list[CodingRun]:
        with self._db.session() as session:
            query = select(CodingRunOrm).where(
                CodingRunOrm.workspace_id == workspace_id,
                CodingRunOrm.owner_user_id == owner_user_id,
            )
            if conversation_id is not None:
                query = query.where(CodingRunOrm.conversation_id == conversation_id)
            return [
                self._run_contract(row)
                for row in session.scalars(
                    query.order_by(CodingRunOrm.created_at.desc()).limit(100)
                )
            ]

    def get_admission(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> CodingRunAdmission:
        """Load the immutable server snapshot needed after a worker restart."""

        with self._db.session() as session:
            row = self._owned_run(session, run_id, workspace_id, owner_user_id)
            return CodingRunAdmission.model_validate({
                "run": self._run_contract(row),
                "principal": row.principal_json,
                "policy_snapshot": row.policy_snapshot_json,
                "environment_profile": row.environment_snapshot_json,
            })

    def get_admission_for_worker(self, *, run_id: str) -> CodingRunAdmission:
        """Load a server-admitted snapshot from the opaque Temporal run ID.

        This is deliberately not an API/read-model method. Temporal receives
        only an outbox-issued run ID and reconstructs the owner/workspace from
        the immutable admission row instead of accepting either scope from its
        activity payload.
        """

        with self._db.session() as session:
            row = session.get(CodingRunOrm, run_id)
            if row is None:
                raise CodingRunNotFound()
            return CodingRunAdmission.model_validate({
                "run": self._run_contract(row),
                "principal": row.principal_json,
                "policy_snapshot": row.policy_snapshot_json,
                "environment_profile": row.environment_snapshot_json,
            })

    def events(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        after: int = 0,
        limit: int = 200,
    ) -> list[CodingRunEvent]:
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("coding_run_event_cursor_invalid")
        with self._db.session() as session:
            self._owned_run(session, run_id, workspace_id, owner_user_id)
            rows = session.scalars(
                select(CodingRunEventOrm)
                .where(
                    CodingRunEventOrm.run_id == run_id,
                    CodingRunEventOrm.workspace_id == workspace_id,
                    CodingRunEventOrm.owner_user_id == owner_user_id,
                    CodingRunEventOrm.sequence > after,
                )
                .order_by(CodingRunEventOrm.sequence)
                .limit(limit)
            )
            return [self._event_contract(row) for row in rows]

    def get_task(
        self,
        *,
        task_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> AgentTask:
        with self._db.session() as session:
            return self._task_contract(
                self._owned_task(session, task_id, workspace_id, owner_user_id)
            )

    def tasks(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> list[AgentTask]:
        with self._db.session() as session:
            self._owned_run(session, run_id, workspace_id, owner_user_id)
            rows = session.scalars(
                select(CodingAgentTaskOrm)
                .where(
                    CodingAgentTaskOrm.run_id == run_id,
                    CodingAgentTaskOrm.workspace_id == workspace_id,
                    CodingAgentTaskOrm.owner_user_id == owner_user_id,
                )
                .order_by(CodingAgentTaskOrm.created_at, CodingAgentTaskOrm.id)
            )
            return [self._task_contract(row) for row in rows]

    def transition_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        phase: CodingRunPhase,
        status: CodingRunStatus,
        event_type: str,
        reason_code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> CodingRun:
        """Persist one lifecycle boundary before another activity may start.

        Terminal rows are immutable. Replaying the same boundary is a no-op;
        a conflicting late worker cannot revive or rewrite a completed run.
        """

        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            terminal = {
                CodingRunStatus.SUCCEEDED,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
                CodingRunStatus.REJECTED,
            }
            if CodingRunStatus(run.status) in terminal:
                if (
                    run.phase == phase.value
                    and run.status == status.value
                    and run.reason_code == reason_code
                ):
                    return self._run_contract(run)
                raise CodingRunConflict("coding_run_is_terminal")
            if (
                run.phase == phase.value
                and run.status == status.value
                and run.reason_code == reason_code
            ):
                return self._run_contract(run)
            run.phase = phase.value
            run.status = status.value
            run.reason_code = reason_code
            run.updated_at = utcnow()
            if status in terminal:
                run.completed_at = run.updated_at
            self._append_event(session, run, event_type, payload=payload)
            session.commit()
            return self._run_contract(run)

    def record_chat_event(
        self,
        *,
        run_id: str,
        task_id: str,
        workspace_id: str,
        owner_user_id: str,
        event_type: str,
        event_key: str,
        payload: dict[str, Any],
    ) -> None:
        if event_type not in {
            "agent.tool_started",
            "agent.tool_completed",
            "agent.message",
            "team.member_started",
            "team.member_completed",
            "team.coordinator_decision",
        }:
            raise CodingRunConflict("coding_chat_event_type_invalid")
        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            task = self._owned_task(session, task_id, workspace_id, owner_user_id, lock=True)
            if task.run_id != run.id:
                raise CodingRunConflict("coding_task_run_mismatch")
            existing = session.scalar(
                select(CodingRunEventOrm).where(
                    CodingRunEventOrm.run_id == run_id,
                    CodingRunEventOrm.task_id == task_id,
                    CodingRunEventOrm.type == event_type,
                    CodingRunEventOrm.payload["event_key"].as_string() == event_key,
                )
            )
            if existing is not None:
                return
            self._append_event(
                session,
                run,
                event_type,
                task_id=task_id,
                payload={**payload, "event_key": event_key},
            )
            session.commit()

    def create_task(self, task: AgentTask) -> AgentTask:
        """Persist a child task without allowing cross-run or cross-owner links."""

        with self._write_session() as session:
            self._owned_run(
                session,
                task.run_id,
                task.workspace_id,
                task.owner_user_id,
                lock=True,
            )
            if task.parent_task_id is not None:
                parent = self._owned_task(
                    session,
                    task.parent_task_id,
                    task.workspace_id,
                    task.owner_user_id,
                    lock=True,
                )
                if parent.run_id != task.run_id:
                    raise CodingRunConflict("coding_task_parent_run_mismatch")
            row = self._task_orm(task)
            session.add(row)
            try:
                session.flush([row])
            except IntegrityError as error:
                session.rollback()
                raise CodingRunConflict("coding_task_already_exists") from error
            run = self._owned_run(
                session,
                task.run_id,
                task.workspace_id,
                task.owner_user_id,
                lock=True,
            )
            self._append_event(session, run, "task.created", task_id=task.id)
            session.commit()
            return self._task_contract(row)

    def checkpoint_task(
        self,
        task: AgentTask,
        *,
        expected_version: int,
        event_type: str = "task.checkpointed",
    ) -> AgentTask:
        """Compare-and-swap a task and append its progress event atomically."""

        if expected_version < 1:
            raise ValueError("coding_task_version_invalid")
        with self._write_session() as session:
            run = self._owned_run(
                session,
                task.run_id,
                task.workspace_id,
                task.owner_user_id,
                lock=True,
            )
            values = self._task_values(task)
            values["version"] = CodingAgentTaskOrm.version + 1
            values["updated_at"] = utcnow()
            result = session.execute(
                update(CodingAgentTaskOrm)
                .where(
                    CodingAgentTaskOrm.id == task.id,
                    CodingAgentTaskOrm.run_id == task.run_id,
                    CodingAgentTaskOrm.workspace_id == task.workspace_id,
                    CodingAgentTaskOrm.owner_user_id == task.owner_user_id,
                    CodingAgentTaskOrm.version == expected_version,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if int(getattr(result, "rowcount", -1)) != 1:
                session.rollback()
                raise CodingTaskConflict("coding_task_stale_version")
            row = self._owned_task(
                session,
                task.id,
                task.workspace_id,
                task.owner_user_id,
                lock=True,
            )
            self._append_event(
                session,
                run,
                event_type,
                task_id=task.id,
                payload={
                    "status": row.status,
                    "iterations_used": row.iterations_used,
                    "version": row.version,
                },
            )
            session.commit()
            return self._task_contract(row)

    def cancel(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> CodingRun:
        """Durably request cancellation once; the worker receives it via outbox."""

        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            if run.status not in {
                CodingRunStatus.SUCCEEDED,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
                CodingRunStatus.REJECTED,
                CodingRunStatus.CANCEL_REQUESTED,
            }:
                run.status = CodingRunStatus.CANCEL_REQUESTED.value
                run.updated_at = utcnow()
                self._append_event(session, run, "run.cancel_requested")
                session.add(
                    CodingRunOutboxOrm(
                        id=f"cancel:{run_id}",
                        workspace_id=workspace_id,
                        owner_user_id=owner_user_id,
                        run_id=run_id,
                        kind="cancel",
                        payload={},
                        delivered=False,
                        attempts=0,
                        created_at=utcnow(),
                    )
                )
                session.commit()
            return self._run_contract(run)

    def stop_with_partial_result(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        reason_code: str,
        partial_result: CodingPartialResult,
        cancelled: bool,
    ) -> CodingRun:
        """Persist the first terminal stop and close publication admission.

        Callers cancel remote handles before this method. The terminal state is
        idempotent, so a late worker report cannot replace the first partial
        result or reopen a run for publication.
        """

        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            if run.status not in {
                CodingRunStatus.SUCCEEDED,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
                CodingRunStatus.REJECTED,
            }:
                run.status = (
                    CodingRunStatus.CANCELLED.value if cancelled else CodingRunStatus.FAILED.value
                )
                run.phase = "completed"
                run.reason_code = reason_code
                run.partial_result_json = partial_result.model_dump(mode="json")
                run.completed_at = utcnow()
                run.updated_at = run.completed_at
                session.execute(
                    update(CodingAgentTaskOrm)
                    .where(
                        CodingAgentTaskOrm.run_id == run_id,
                        CodingAgentTaskOrm.status.in_(["queued", "running", "cancel_requested"]),
                    )
                    .values(status=run.status, version=CodingAgentTaskOrm.version + 1)
                )
                self._append_event(
                    session,
                    run,
                    "run.cancelled" if cancelled else "run.expired",
                    payload={"reason_code": reason_code},
                )
                session.commit()
            return self._run_contract(run)

    def record_model_usage(
        self,
        *,
        run_id: str,
        task_id: str,
        invocation_id: str,
        workspace_id: str,
        owner_user_id: str,
        usage: dict[str, Any],
    ) -> None:
        """Receipt and conversation totals commit together, exactly once per invocation."""
        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            task = self._owned_task(session, task_id, workspace_id, owner_user_id)
            if task.run_id != run_id:
                raise CodingRunConflict("coding_iteration_task_scope_mismatch")
            row = session.scalar(
                select(CodingIterationReservationOrm)
                .where(
                    CodingIterationReservationOrm.run_id == run_id,
                    CodingIterationReservationOrm.invocation_id == invocation_id,
                )
                .with_for_update()
            )
            if row is None or row.task_id != task_id:
                raise CodingRunConflict("coding_iteration_reservation_missing")
            if row.usage_json is not None:
                return
            row.usage_json = usage
            if run.conversation_id:
                increments = {
                    "llm_input_tokens": usage["input_tokens"],
                    "llm_output_tokens": usage["output_tokens"],
                    "llm_cached_input_tokens": usage["cached_input_tokens"],
                    "llm_cache_write_input_tokens": usage["cache_write_input_tokens"],
                    "llm_cache_usage_reported_calls": usage["cache_usage_reported_calls"],
                }
                session.execute(
                    update(ChatConversationOrm)
                    .where(
                        ChatConversationOrm.id == run.conversation_id,
                        ChatConversationOrm.owner_user_id == owner_user_id,
                        ChatConversationOrm.workspace_id == workspace_id,
                    )
                    .values(**{
                        name: getattr(ChatConversationOrm, name) + value
                        for name, value in increments.items()
                    })
                )
            session.commit()

    def reserve_iteration(
        self,
        *,
        run_id: str,
        task_id: str,
        executing_agent_profile_id: str,
        invocation_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> CodingIterationReservation:
        """Atomically reserve one model iteration for a stable invocation ID.

        The first reservation fixes the deadline for the profile. A retry with
        the same invocation ID is returned before budget/deadline checks, so a
        Temporal activity retry never receives a fresh model-call allowance.
        """

        if not invocation_id.strip():
            raise ValueError("coding_invocation_id_required")
        with self._write_session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id, lock=True)
            task = self._owned_task(session, task_id, workspace_id, owner_user_id, lock=True)
            if task.run_id != run_id or task.agent_profile_id != executing_agent_profile_id:
                raise CodingRunConflict("coding_iteration_task_scope_mismatch")
            budget_owner_agent_id, max_iterations, max_runtime_seconds = self._budget_settings(run)
            ledger = session.scalar(
                select(CodingBudgetLedgerOrm)
                .where(
                    CodingBudgetLedgerOrm.run_id == run_id,
                    CodingBudgetLedgerOrm.budget_owner_agent_id == budget_owner_agent_id,
                )
                .with_for_update()
            )
            now = utcnow()
            if ledger is None:
                deadline = self._first_attempt_deadline(
                    run=run,
                    task=task,
                    max_runtime_seconds=max_runtime_seconds,
                    now=now,
                )
                ledger = CodingBudgetLedgerOrm(
                    id=str(
                        uuid5(NAMESPACE_URL, f"rhetorica/coding/{run_id}/{budget_owner_agent_id}")
                    ),
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    run_id=run_id,
                    budget_owner_agent_id=budget_owner_agent_id,
                    max_iterations=max_iterations,
                    reserved_iterations=0,
                    first_attempt_at=None,
                    deadline=deadline,
                    updated_at=now,
                )
                session.add(ledger)
                session.flush([ledger])
            elif ledger.max_iterations != max_iterations:
                raise CodingRunConflict("coding_budget_snapshot_limit_mismatch")
            deadline = _as_utc(ledger.deadline)
            existing = session.scalar(
                select(CodingIterationReservationOrm).where(
                    CodingIterationReservationOrm.run_id == run_id,
                    CodingIterationReservationOrm.budget_owner_agent_id == budget_owner_agent_id,
                    CodingIterationReservationOrm.invocation_id == invocation_id,
                )
            )
            if existing is not None:
                return self._reservation_contract(existing, deadline=deadline)
            if now >= deadline:
                raise CodingBudgetExhausted("coding_runtime_limit_reached")
            if task.iterations_used >= task.max_iterations:
                raise CodingBudgetExhausted()
            if ledger.reserved_iterations >= ledger.max_iterations:
                raise CodingBudgetExhausted()
            iteration = ledger.reserved_iterations + 1
            reservation = CodingIterationReservationOrm(
                id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"rhetorica/coding/{run_id}/{budget_owner_agent_id}/{invocation_id}",
                    )
                ),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                run_id=run_id,
                task_id=task_id,
                budget_owner_agent_id=budget_owner_agent_id,
                invocation_id=invocation_id,
                iteration=iteration,
                reserved_at=now,
            )
            ledger.reserved_iterations = iteration
            ledger.first_attempt_at = ledger.first_attempt_at or now
            ledger.updated_at = now
            task.iterations_used += 1
            task.version += 1
            task.updated_at = now
            task.deadline = task.deadline or min(
                _as_utc(run.deadline),
                now + timedelta(seconds=task.max_runtime_seconds),
            )
            session.add(reservation)
            self._append_event(
                session,
                run,
                "budget.reserved",
                task_id=task_id,
                payload={
                    "budget_owner_agent_id": budget_owner_agent_id,
                    "executing_agent_profile_id": executing_agent_profile_id,
                    "iteration": iteration,
                    "deadline": deadline.isoformat(),
                },
            )
            session.commit()
            return self._reservation_contract(reservation, deadline=deadline)

    def budget_ledgers(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> list[CodingBudgetLedger]:
        with self._db.session() as session:
            self._owned_run(session, run_id, workspace_id, owner_user_id)
            rows = session.scalars(
                select(CodingBudgetLedgerOrm)
                .where(
                    CodingBudgetLedgerOrm.run_id == run_id,
                    CodingBudgetLedgerOrm.workspace_id == workspace_id,
                    CodingBudgetLedgerOrm.owner_user_id == owner_user_id,
                )
                .order_by(CodingBudgetLedgerOrm.budget_owner_agent_id)
            )
            return [self._ledger_contract(row) for row in rows]

    def record_candidate(self, candidate: CandidateManifest) -> CodingRun:
        """Persist the one immutable candidate to which QA evidence may refer."""

        with self._write_session() as session:
            run = self._owned_run(
                session,
                candidate.run_id,
                candidate.workspace_id,
                candidate.owner_user_id,
                lock=True,
            )
            if run.status in {
                CodingRunStatus.SUCCEEDED,
                CodingRunStatus.FAILED,
                CodingRunStatus.CANCELLED,
                CodingRunStatus.REJECTED,
            }:
                raise CodingRunConflict("coding_run_is_terminal")
            existing = session.scalar(
                select(CodingCandidateOrm)
                .where(
                    CodingCandidateOrm.run_id == candidate.run_id,
                    CodingCandidateOrm.digest == candidate.digest,
                )
                .with_for_update()
            )
            if existing is not None:
                if self._candidate_contract(existing) != candidate:
                    raise CodingRunConflict("coding_candidate_digest_reused")
                return self._run_contract(run)
            session.add(self._candidate_orm(candidate))
            run.candidate_json = candidate.model_dump(mode="json")
            run.validation_report_id = None
            run.phase = "validating"
            run.updated_at = utcnow()
            self._append_event(
                session,
                run,
                "candidate.recorded",
                payload={"artifact_id": candidate.artifact_id, "digest": candidate.digest},
            )
            session.commit()
            return self._run_contract(run)

    def candidates(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> list[CandidateManifest]:
        """Return the immutable candidate history, oldest first, for recovery/audit."""

        with self._db.session() as session:
            self._owned_run(session, run_id, workspace_id, owner_user_id)
            rows = session.scalars(
                select(CodingCandidateOrm)
                .where(
                    CodingCandidateOrm.run_id == run_id,
                    CodingCandidateOrm.workspace_id == workspace_id,
                    CodingCandidateOrm.owner_user_id == owner_user_id,
                )
                .order_by(CodingCandidateOrm.created_at, CodingCandidateOrm.id)
            )
            return [self._candidate_contract(row) for row in rows]

    def record_validation_evidence(
        self, evidence: ServerValidationEvidence
    ) -> ServerValidationEvidence:
        """Store a compact, server-created receipt for exactly one candidate.

        A developer response cannot call this with raw output: the workflow
        activity creates the receipt from a completed sandbox/dbt operation.
        """

        with self._write_session() as session:
            run = self._owned_run(
                session,
                evidence.run_id,
                evidence.workspace_id,
                evidence.owner_user_id,
                lock=True,
            )
            self._require_candidate_digest(run, evidence.candidate_digest)
            if evidence.task_id is not None:
                task = self._owned_task(
                    session,
                    evidence.task_id,
                    evidence.workspace_id,
                    evidence.owner_user_id,
                    lock=True,
                )
                if task.run_id != evidence.run_id:
                    raise CodingRunConflict("coding_validation_task_run_mismatch")
            existing = session.get(CodingValidationEvidenceOrm, evidence.id)
            if existing is not None:
                persisted = self._evidence_contract(existing)
                if persisted != evidence:
                    raise CodingRunConflict("coding_validation_evidence_id_reused")
                return persisted
            session.add(self._evidence_orm(evidence))
            self._append_event(
                session,
                run,
                "validation.evidence_recorded",
                task_id=evidence.task_id,
                payload={
                    "receipt_id": evidence.id,
                    "name": evidence.name,
                    "status": evidence.status,
                },
            )
            session.commit()
            return evidence

    def validation_evidence(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        candidate_digest: str,
    ) -> list[ServerValidationEvidence]:
        with self._db.session() as session:
            run = self._owned_run(session, run_id, workspace_id, owner_user_id)
            self._require_candidate_digest(run, candidate_digest)
            rows = session.scalars(
                select(CodingValidationEvidenceOrm)
                .where(
                    CodingValidationEvidenceOrm.run_id == run_id,
                    CodingValidationEvidenceOrm.workspace_id == workspace_id,
                    CodingValidationEvidenceOrm.owner_user_id == owner_user_id,
                    CodingValidationEvidenceOrm.candidate_digest == candidate_digest,
                )
                .order_by(CodingValidationEvidenceOrm.name, CodingValidationEvidenceOrm.id)
            )
            return [self._evidence_contract(row) for row in rows]

    def save_validation_report(
        self,
        *,
        report: ValidationReport,
        owner_user_id: str,
    ) -> ValidationReport:
        """Atomically bind a server-assembled report to its persisted candidate."""

        with self._write_session() as session:
            run = self._owned_run(
                session,
                report.run_id,
                report.workspace_id,
                owner_user_id,
                lock=True,
            )
            self._require_candidate_digest(run, report.candidate_digest)
            if report.qa_verdict is None:
                raise CodingRunConflict("coding_validation_qa_verdict_missing")
            qa_task = self._owned_task(
                session,
                report.qa_verdict.task_id,
                report.workspace_id,
                owner_user_id,
                lock=True,
            )
            if (
                qa_task.run_id != report.run_id
                or qa_task.role != "qa"
                or qa_task.agent_profile_id != report.qa_agent_id
                or report.qa_verdict.qa_agent_id != report.qa_agent_id
                or report.qa_verdict.candidate_digest != report.candidate_digest
            ):
                raise CodingRunConflict("coding_validation_qa_scope_mismatch")
            existing = session.scalar(
                select(CodingValidationReportOrm)
                .where(
                    CodingValidationReportOrm.run_id == report.run_id,
                    CodingValidationReportOrm.candidate_digest == report.candidate_digest,
                )
                .with_for_update()
            )
            if existing is not None:
                persisted = self._report_contract(existing)
                if persisted == report:
                    return persisted
                if not self._adds_independent_review(persisted, report) or (
                    run.publication_receipt_id is not None
                ):
                    raise CodingRunConflict("coding_validation_report_already_recorded")
                # Another reviewer checked this candidate; earlier reviews stay as checks.
                session.delete(existing)
                session.flush()
            session.add(
                CodingValidationReportOrm(
                    id=report.id,
                    workspace_id=report.workspace_id,
                    owner_user_id=owner_user_id,
                    run_id=report.run_id,
                    candidate_digest=report.candidate_digest,
                    report_digest=report.report_digest,
                    qa_agent_id=report.qa_agent_id,
                    qa_verdict_json=report.qa_verdict.model_dump(mode="json"),
                    checks_json=[check.model_dump(mode="json") for check in report.checks],
                    created_at=report.created_at,
                )
            )
            run.validation_report_id = report.id
            run.updated_at = utcnow()
            self._append_event(
                session,
                run,
                "validation.report_recorded",
                task_id=qa_task.id,
                payload={"report_id": report.id, "digest": report.report_digest},
            )
            session.commit()
            return report

    def record_publication_receipt(self, receipt: PublicationReceipt) -> CodingRun:
        """Attach the durable GitLab receipt after its own ledger committed it."""

        with self._write_session() as session:
            run = self._owned_run(
                session,
                receipt.run_id,
                receipt.workspace_id,
                receipt.owner_user_id,
                lock=True,
            )
            self._require_candidate_digest(run, receipt.candidate_digest)
            if run.publication_receipt_id is not None:
                if run.publication_receipt_id != receipt.id:
                    raise CodingRunConflict("coding_publication_receipt_already_recorded")
                return self._run_contract(run)
            if run.validation_report_id is None:
                raise CodingRunConflict("coding_validation_report_not_recorded")
            report = session.get(CodingValidationReportOrm, run.validation_report_id)
            if report is None or report.report_digest != receipt.validation_report_digest:
                raise CodingRunConflict("coding_publication_report_mismatch")
            if run.status == CodingRunStatus.CANCEL_REQUESTED.value:
                run.publication_receipt_id = receipt.id
                run.updated_at = utcnow()
                self._append_event(
                    session,
                    run,
                    "publication.receipt_recorded_after_cancel_request",
                    payload={"receipt_id": receipt.id, "state": receipt.state},
                )
                session.commit()
                return self._run_contract(run)
            if run.status in {
                CodingRunStatus.SUCCEEDED.value,
                CodingRunStatus.FAILED.value,
                CodingRunStatus.CANCELLED.value,
                CodingRunStatus.REJECTED.value,
            }:
                # A publisher can finish after cancellation or another terminal
                # decision. Keep that receipt observable, but never reopen or
                # rewrite the durable outcome selected first.
                run.publication_receipt_id = receipt.id
                run.updated_at = utcnow()
                self._append_event(
                    session,
                    run,
                    "publication.receipt_recorded_after_terminal",
                    payload={"receipt_id": receipt.id, "state": receipt.state},
                )
                session.commit()
                return self._run_contract(run)
            run.publication_receipt_id = receipt.id
            run.phase = CodingRunPhase.COMPLETED.value
            run.status = CodingRunStatus.SUCCEEDED.value
            run.reason_code = receipt.reason_code
            run.completed_at = utcnow()
            run.updated_at = run.completed_at
            self._append_event(
                session,
                run,
                "publication.recorded",
                payload={"receipt_id": receipt.id, "state": receipt.state},
            )
            session.commit()
            return self._run_contract(run)

    def validation_report(
        self,
        *,
        report_id: str,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> ValidationReport:
        with self._db.session() as session:
            self._owned_run(session, run_id, workspace_id, owner_user_id)
            row = session.scalar(
                select(CodingValidationReportOrm).where(
                    CodingValidationReportOrm.id == report_id,
                    CodingValidationReportOrm.run_id == run_id,
                    CodingValidationReportOrm.workspace_id == workspace_id,
                    CodingValidationReportOrm.owner_user_id == owner_user_id,
                )
            )
            if row is None:
                raise CodingRunNotFound("coding_validation_report_not_found")
            return self._report_contract(row)

    def pending_outbox(self, *, limit: int = 100) -> list[CodingRunOutboxEntry]:
        if not 1 <= limit <= 500:
            raise ValueError("coding_outbox_limit_invalid")
        with self._db.session() as session:
            rows = session.scalars(
                select(CodingRunOutboxOrm)
                .where(CodingRunOutboxOrm.delivered.is_(False))
                .order_by(CodingRunOutboxOrm.created_at, CodingRunOutboxOrm.id)
                .limit(limit)
            )
            return [self._outbox_contract(row) for row in rows]

    def record_outbox_attempt(
        self,
        *,
        entry: CodingRunOutboxEntry,
        delivered: bool,
        error: str | None = None,
    ) -> CodingRunOutboxEntry | None:
        """Fence outbox acknowledgements by scope and the previous attempt count."""

        values: dict[str, Any] = {
            "attempts": CodingRunOutboxOrm.attempts + 1,
            "last_error": None if delivered else error,
        }
        if delivered:
            values["delivered"] = True
        with self._db.session() as session:
            result = session.execute(
                update(CodingRunOutboxOrm)
                .where(
                    CodingRunOutboxOrm.id == entry.id,
                    CodingRunOutboxOrm.workspace_id == entry.workspace_id,
                    CodingRunOutboxOrm.owner_user_id == entry.owner_user_id,
                    CodingRunOutboxOrm.run_id == entry.run_id,
                    CodingRunOutboxOrm.delivered.is_(False),
                    CodingRunOutboxOrm.attempts == entry.attempts,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if int(getattr(result, "rowcount", -1)) != 1:
                session.rollback()
                return None
            session.commit()
            row = session.get(CodingRunOutboxOrm, entry.id, populate_existing=True)
            return None if row is None else self._outbox_contract(row)

    def _duplicate_admission(self, admission: CodingRunAdmission) -> CodingRunAdmissionResult:
        run = admission.run
        with self._db.session() as session:
            existing = session.scalar(
                select(CodingRunOrm).where(
                    CodingRunOrm.workspace_id == run.workspace_id,
                    CodingRunOrm.owner_user_id == run.owner_user_id,
                    CodingRunOrm.idempotency_key == run.idempotency_key,
                )
            )
            if existing is None:
                raise CodingRunConflict("coding_run_admission_conflict")
            if existing.input_digest != run.input_digest:
                raise CodingRunConflict("coding_idempotency_key_reused")
            return CodingRunAdmissionResult(
                run=self._run_contract(existing),
                created=False,
                workflow_id=self.workflow_id(existing.id),
            )

    def _write_session(self) -> _CodingWriteSession:
        lock: AbstractContextManager[Any] = (
            self._sqlite_write_lock if self._db.engine.dialect.name == "sqlite" else nullcontext()
        )
        return _CodingWriteSession(lock, self._db)

    @staticmethod
    def _owned_run(
        session: Session,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        lock: bool = False,
    ) -> CodingRunOrm:
        query = select(CodingRunOrm).where(
            CodingRunOrm.id == run_id,
            CodingRunOrm.workspace_id == workspace_id,
            CodingRunOrm.owner_user_id == owner_user_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise CodingRunNotFound()
        return row

    @staticmethod
    def _owned_task(
        session: Session,
        task_id: str,
        workspace_id: str,
        owner_user_id: str,
        *,
        lock: bool = False,
    ) -> CodingAgentTaskOrm:
        query = select(CodingAgentTaskOrm).where(
            CodingAgentTaskOrm.id == task_id,
            CodingAgentTaskOrm.workspace_id == workspace_id,
            CodingAgentTaskOrm.owner_user_id == owner_user_id,
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise CodingRunNotFound("coding_agent_task_not_found")
        return row

    @staticmethod
    def _append_event(
        session: Session,
        run: CodingRunOrm,
        event_type: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        compact_payload = _bounded_event_payload(payload or {})
        run.event_sequence += 1
        session.add(
            CodingRunEventOrm(
                run_id=run.id,
                sequence=run.event_sequence,
                workspace_id=run.workspace_id,
                owner_user_id=run.owner_user_id,
                type=event_type,
                task_id=task_id,
                payload=compact_payload,
                created_at=utcnow(),
            )
        )

    @staticmethod
    def _run_orm(admission: CodingRunAdmission) -> CodingRunOrm:
        run = admission.run
        return CodingRunOrm(
            id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            source=run.source,
            conversation_id=run.conversation_id,
            scenario_id=run.scenario_id,
            root_agent_id=run.root_agent_id,
            developer_agent_id=run.developer_agent_id,
            qa_agent_id=run.qa_agent_id,
            team_snapshot_json=(
                run.team_snapshot.model_dump(mode="json") if run.team_snapshot is not None else None
            ),
            idempotency_key=run.idempotency_key,
            input_digest=run.input_digest,
            phase=run.phase,
            status=run.status,
            reason_code=run.reason_code,
            repository_json=run.repository.model_dump(mode="json"),
            data_warehouse_json=(
                None if run.data_warehouse is None else run.data_warehouse.model_dump(mode="json")
            ),
            environment_profile_id=run.environment_profile_id,
            environment_profile_revision=run.environment_profile_revision,
            policy_revision=run.policy_revision,
            candidate_json=None if run.candidate is None else run.candidate.model_dump(mode="json"),
            validation_report_id=run.validation_report_id,
            publication_receipt_id=run.publication_receipt_id,
            partial_result_json=(
                None if run.partial_result is None else run.partial_result.model_dump(mode="json")
            ),
            principal_json=admission.principal.model_dump(mode="json"),
            policy_snapshot_json=admission.policy_snapshot.model_dump(mode="json"),
            environment_snapshot_json=admission.environment_profile.model_dump(mode="json"),
            event_sequence=0,
            deadline=run.deadline,
            created_at=run.created_at,
            updated_at=run.created_at,
            completed_at=None,
        )

    @staticmethod
    def _task_orm(task: AgentTask) -> CodingAgentTaskOrm:
        return CodingAgentTaskOrm(id=task.id, **CodingRunRepository._task_values(task))

    @staticmethod
    def _candidate_orm(candidate: CandidateManifest) -> CodingCandidateOrm:
        return CodingCandidateOrm(
            id=str(uuid5(NAMESPACE_URL, f"rhetorica/coding/{candidate.run_id}/{candidate.digest}")),
            workspace_id=candidate.workspace_id,
            owner_user_id=candidate.owner_user_id,
            run_id=candidate.run_id,
            artifact_id=candidate.artifact_id,
            digest=candidate.digest,
            base_sha=candidate.base_sha,
            changed_paths=candidate.changed_paths,
            size_bytes=candidate.size_bytes,
            created_at=candidate.created_at,
        )

    @staticmethod
    def _evidence_orm(evidence: ServerValidationEvidence) -> CodingValidationEvidenceOrm:
        return CodingValidationEvidenceOrm(
            id=evidence.id,
            workspace_id=evidence.workspace_id,
            owner_user_id=evidence.owner_user_id,
            run_id=evidence.run_id,
            task_id=evidence.task_id,
            candidate_digest=evidence.candidate_digest,
            name=evidence.name,
            source=evidence.source,
            status=evidence.status,
            mandatory=evidence.mandatory,
            exit_code=evidence.exit_code,
            duration_ms=evidence.duration_ms,
            artifact_id=evidence.artifact_id,
            reason=evidence.reason,
            created_at=evidence.created_at,
        )

    @staticmethod
    def _task_values(task: AgentTask) -> dict[str, Any]:
        return {
            "workspace_id": task.workspace_id,
            "owner_user_id": task.owner_user_id,
            "run_id": task.run_id,
            "parent_task_id": task.parent_task_id,
            "agent_profile_id": task.agent_profile_id,
            "role": task.role,
            "team_member_id": task.team_member_id,
            "team_member_label": task.team_member_label,
            "status": task.status,
            "iterations_used": task.iterations_used,
            "max_iterations": task.max_iterations,
            "max_runtime_seconds": task.max_runtime_seconds,
            "attempts": task.attempts,
            "instruction_artifact_id": task.instruction_artifact_id,
            "skill_version_digests": task.skill_version_digests,
            "skill_version_bindings": [
                binding.model_dump(mode="json") for binding in task.skill_version_bindings
            ],
            "skill_read_addresses": [
                address.model_dump(mode="json") for address in task.skill_read_addresses
            ],
            "result_artifact_ids": task.result_artifact_ids,
            "started_at": task.started_at,
            "deadline": task.deadline,
            "checkpoint_ref": task.checkpoint_ref,
            "version": task.version,
        }

    @staticmethod
    def _run_contract(row: CodingRunOrm) -> CodingRun:
        return CodingRun.model_validate({
            "id": row.id,
            "workspace_id": row.workspace_id,
            "owner_user_id": row.owner_user_id,
            "source": row.source,
            "conversation_id": row.conversation_id,
            "scenario_id": row.scenario_id,
            "root_agent_id": row.root_agent_id,
            "developer_agent_id": row.developer_agent_id,
            "qa_agent_id": row.qa_agent_id,
            "team_snapshot": row.team_snapshot_json,
            "idempotency_key": row.idempotency_key,
            "input_digest": row.input_digest,
            "phase": row.phase,
            "status": row.status,
            "reason_code": row.reason_code,
            "repository": row.repository_json,
            "data_warehouse": row.data_warehouse_json,
            "environment_profile_id": row.environment_profile_id,
            "environment_profile_revision": row.environment_profile_revision,
            "policy_revision": row.policy_revision,
            "candidate": row.candidate_json,
            "validation_report_id": row.validation_report_id,
            "publication_receipt_id": row.publication_receipt_id,
            "partial_result": row.partial_result_json,
            "created_at": _as_utc(row.created_at),
            "deadline": _as_utc(row.deadline),
        })

    @staticmethod
    def _task_contract(row: CodingAgentTaskOrm) -> AgentTask:
        return AgentTask.model_validate({
            "id": row.id,
            "workspace_id": row.workspace_id,
            "owner_user_id": row.owner_user_id,
            "run_id": row.run_id,
            "parent_task_id": row.parent_task_id,
            "agent_profile_id": row.agent_profile_id,
            "role": row.role,
            "team_member_id": row.team_member_id,
            "team_member_label": row.team_member_label,
            "status": row.status,
            "iterations_used": row.iterations_used,
            "max_iterations": row.max_iterations,
            "max_runtime_seconds": row.max_runtime_seconds,
            "attempts": row.attempts,
            "instruction_artifact_id": row.instruction_artifact_id,
            "skill_version_digests": row.skill_version_digests or [],
            "skill_version_bindings": row.skill_version_bindings or [],
            "skill_read_addresses": row.skill_read_addresses or [],
            "result_artifact_ids": row.result_artifact_ids or [],
            "started_at": _as_utc_optional(row.started_at),
            "deadline": _as_utc_optional(row.deadline),
            "checkpoint_ref": row.checkpoint_ref,
            "version": row.version,
        })

    @staticmethod
    def _event_contract(row: CodingRunEventOrm) -> CodingRunEvent:
        return CodingRunEvent(
            run_id=row.run_id,
            sequence=row.sequence,
            type=row.type,
            task_id=row.task_id,
            payload=row.payload or {},
            created_at=_as_utc(row.created_at),
        )

    @staticmethod
    def _evidence_contract(row: CodingValidationEvidenceOrm) -> ServerValidationEvidence:
        return ServerValidationEvidence(
            id=row.id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            run_id=row.run_id,
            task_id=row.task_id,
            candidate_digest=row.candidate_digest,
            name=row.name,
            source=cast(Literal["sandbox", "dbt", "system"], row.source),
            status=ValidationStatus(row.status),
            mandatory=row.mandatory,
            exit_code=row.exit_code,
            duration_ms=row.duration_ms,
            artifact_id=row.artifact_id,
            reason=row.reason,
            created_at=_as_utc(row.created_at),
        )

    @staticmethod
    def _candidate_contract(row: CodingCandidateOrm) -> CandidateManifest:
        return CandidateManifest(
            artifact_id=row.artifact_id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            run_id=row.run_id,
            digest=row.digest,
            base_sha=row.base_sha,
            changed_paths=row.changed_paths or [],
            size_bytes=row.size_bytes,
            created_at=_as_utc(row.created_at),
        )

    @staticmethod
    def _report_contract(row: CodingValidationReportOrm) -> ValidationReport:
        return ValidationReport.model_validate({
            "id": row.id,
            "workspace_id": row.workspace_id,
            "run_id": row.run_id,
            "candidate_digest": row.candidate_digest,
            "report_digest": row.report_digest,
            "checks": row.checks_json or [],
            "qa_agent_id": row.qa_agent_id,
            "qa_verdict": row.qa_verdict_json,
            "created_at": _as_utc(row.created_at),
        })

    @staticmethod
    def _adds_independent_review(previous: ValidationReport, current: ValidationReport) -> bool:
        def reviews(report: ValidationReport) -> set[str]:
            return {
                check.name
                for check in report.checks
                if check.name.startswith("independent_review:") and check.receipt_id is not None
            }

        return reviews(previous) < reviews(current)

    @staticmethod
    def _require_candidate_digest(run: CodingRunOrm, candidate_digest: str) -> None:
        if run.candidate_json is None:
            raise CodingRunConflict("coding_candidate_not_recorded")
        if CandidateManifest.model_validate(run.candidate_json).digest != candidate_digest:
            raise CodingRunConflict("coding_candidate_digest_mismatch")

    @staticmethod
    def _outbox_contract(row: CodingRunOutboxOrm) -> CodingRunOutboxEntry:
        return CodingRunOutboxEntry(
            id=row.id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            run_id=row.run_id,
            kind=cast(Literal["start", "cancel"], row.kind),
            payload=row.payload or {},
            delivered=row.delivered,
            attempts=row.attempts,
            last_error=row.last_error,
            created_at=_as_utc(row.created_at),
        )

    @staticmethod
    def _ledger_contract(row: CodingBudgetLedgerOrm) -> CodingBudgetLedger:
        return CodingBudgetLedger(
            run_id=row.run_id,
            budget_owner_agent_id=row.budget_owner_agent_id,
            max_iterations=row.max_iterations,
            reserved_iterations=row.reserved_iterations,
            first_attempt_at=_as_utc_optional(row.first_attempt_at),
            deadline=_as_utc(row.deadline),
        )

    @staticmethod
    def _reservation_contract(
        row: CodingIterationReservationOrm,
        *,
        deadline: datetime | None = None,
    ) -> CodingIterationReservation:
        if deadline is None:
            raise RuntimeError("coding_reservation_deadline_required")
        return CodingIterationReservation(
            id=row.id,
            run_id=row.run_id,
            budget_owner_agent_id=row.budget_owner_agent_id,
            invocation_id=row.invocation_id,
            iteration=row.iteration,
            deadline=_as_utc(deadline),
            reserved_at=_as_utc(row.reserved_at),
        )

    @staticmethod
    def _first_attempt_deadline(
        *,
        run: CodingRunOrm,
        task: CodingAgentTaskOrm,
        max_runtime_seconds: int,
        now: datetime,
    ) -> datetime:
        environment_limit = int(run.environment_snapshot_json["max_runtime_seconds"])
        candidates = [
            _as_utc(run.deadline),
            now + timedelta(seconds=max_runtime_seconds),
            now + timedelta(seconds=environment_limit),
        ]
        if task.deadline is not None:
            candidates.append(_as_utc(task.deadline))
        return min(candidates)

    @staticmethod
    def _budget_settings(run: CodingRunOrm) -> tuple[str, int, int]:
        """Return the immutable root-agent limits captured at admission.

        The executing task is intentionally absent from this calculation:
        delegating to developer or QA must not manufacture a second budget.
        A root-less run is a default-assistant run, whose executing policy is
        the only configured policy available at admission.
        """

        snapshot = AgentPolicySnapshot.model_validate(run.policy_snapshot_json)
        budget_owner_agent_id = (
            run.root_agent_id or snapshot.root_agent_id or snapshot.executing_agent_id
        )
        if snapshot.root_agent_id == budget_owner_agent_id and snapshot.root_settings is not None:
            settings = snapshot.root_settings
        elif snapshot.executing_agent_id == budget_owner_agent_id:
            settings = snapshot.executing_settings
        else:
            raise CodingRunConflict("coding_budget_owner_snapshot_mismatch")
        return budget_owner_agent_id, settings.max_iterations, settings.max_runtime_seconds


class _CodingWriteSession:
    """Serialize event cursors on SQLite and use row locks on PostgreSQL."""

    def __init__(self, lock: AbstractContextManager[Any], db: DatabaseRuntime) -> None:
        self._lock = lock
        self._db = db
        self._session: Session | None = None

    def __enter__(self) -> Session:
        self._lock.__enter__()
        self._session = self._db.session()
        if self._db.engine.dialect.name == "sqlite":
            self._session.execute(text("BEGIN IMMEDIATE"))
        return self._session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            raise RuntimeError("coding_write_session_not_open")
        self._session.close()
        self._lock.__exit__(exc_type, exc, traceback)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _as_utc_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


def _bounded_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep Temporal/UI progress useful without admitting evidence bodies."""

    try:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CodingRunConflict("coding_event_payload_invalid") from error
    if len(encoded) > 4_096:
        raise CodingRunConflict("coding_event_payload_too_large")
    return payload
