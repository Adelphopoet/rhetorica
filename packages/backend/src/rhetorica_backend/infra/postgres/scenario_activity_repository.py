"""Fenced activity state changes and durable data ownership links."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.scenario_workflows import TERMINAL_RUN_STATUSES
from rhetorica_backend.domain.files.service import ArtifactService
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import (
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
)
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.file_models import FileArtifactOrm
from rhetorica_backend.infra.postgres.scenario_run_repository import append_run_event
from rhetorica_backend.infra.postgres.scenario_workflow_models import (
    ScenarioArtifactOrm,
    ScenarioExecutionOrm,
    ScenarioOutboxOrm,
    ScenarioStepRunOrm,
    ScenarioWorkflowRunOrm,
    utcnow,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select
from sqlalchemy.orm import Session


def _locked_run(session: Session, run_id: str) -> ScenarioWorkflowRunOrm:
    run = session.scalar(
        select(ScenarioWorkflowRunOrm).where(ScenarioWorkflowRunOrm.id == run_id).with_for_update()
    )
    if run is None:
        raise WorkflowNotFound("scenario_run_not_found")
    return run


def _required_step(session: Session, run_id: str, node_id: str) -> ScenarioStepRunOrm:
    step = session.get(ScenarioStepRunOrm, (run_id, node_id))
    if step is None:
        raise WorkflowNotFound("scenario_step_not_found")
    return step


class ScenarioActivityRepository:
    def __init__(self, db: DatabaseRuntime):
        self.db = db

    def load_run(self, run_id: str) -> ScenarioWorkflowRunOrm:
        with self.db.session() as session:
            run = session.get(ScenarioWorkflowRunOrm, run_id)
            if run is None:
                raise WorkflowNotFound("scenario_run_not_found")
            return run

    def commit_result(
        self,
        run_id: str,
        node_id: str,
        attempt: int,
        data: ScenarioDataStore,
        output: dict[str, Any],
        duration: int,
    ) -> str:
        # Keep the attempt fence while publishing the immutable result manifest. A stale
        # worker cannot overwrite the result after a newer attempt acquired the step.
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            step = _required_step(session, run_id, node_id)
            if step.status == "succeeded" and step.output_ref:
                return step.output_ref
            if step.attempt != attempt:
                raise WorkflowConflict("stale_step_attempt")
            if run.status in TERMINAL_RUN_STATUSES or run.status == "cancel_requested":
                raise WorkflowError("scenario_run_stopped")
            ref = data.put(
                run_id=run_id, node_id=node_id, attempt=0, role="committed", value=output
            )
            step.status = "succeeded"
            step.output_ref = ref
            step.completed_at = utcnow()
            step.diagnostics = {**(step.diagnostics or {}), "duration_ms": duration}
            run.status = "running"
            append_run_event(
                session, run, "step.succeeded", node_id=node_id, payload={"attempt": attempt}
            )
            session.commit()
            return ref

    def load_step(self, run_id: str, node_id: str) -> ScenarioStepRunOrm:
        with self.db.session() as session:
            step = _required_step(session, run_id, node_id)
            if step is None:
                raise WorkflowNotFound("scenario_step_not_found")
            return step

    def commit_operation_result(
        self,
        run_id: str,
        node_id: str,
        attempt: int,
        data: ScenarioDataStore,
        role: str,
        output: dict[str, Any],
    ) -> str:
        """Persist a sub-operation before releasing its external resources."""
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            step = _required_step(session, run_id, node_id)
            if step.attempt != attempt:
                raise WorkflowConflict("stale_step_attempt")
            if run.status in TERMINAL_RUN_STATUSES or run.status == "cancel_requested":
                raise WorkflowError("scenario_run_stopped")
            return data.put(run_id=run_id, node_id=node_id, attempt=0, role=role, value=output)

    def fail_step(self, run_id: str, node_id: str, attempt: int, error: str) -> None:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            step = _required_step(session, run_id, node_id)
            if (
                step is None
                or step.attempt > attempt
                or step.status in {"succeeded", "skipped", "cancelled"}
            ):
                return
            step.attempt = attempt
            step.status = "failed"
            step.error = error
            step.completed_at = utcnow()
            append_run_event(session, run, "step.failed", node_id=node_id, payload={"error": error})
            session.commit()

    def list_steps(self, run_id: str) -> list[ScenarioStepRunOrm]:
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(ScenarioStepRunOrm).where(ScenarioStepRunOrm.run_id == run_id)
                )
            )

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.session() as session:
            rows = session.scalars(
                select(FileArtifactOrm)
                .join(
                    ScenarioArtifactOrm,
                    ScenarioArtifactOrm.artifact_id == FileArtifactOrm.id,
                )
                .where(ScenarioArtifactOrm.run_id == run_id)
            )
            return [
                ArtifactService.to_ref(FileArtifactRepository._to_domain(row)).model_dump(
                    mode="json"
                )
                for row in rows
            ]

    def begin_step(
        self, run_id: str, node_id: str, attempt: int, input_ref: str
    ) -> ScenarioStepRunOrm:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            if run.status in TERMINAL_RUN_STATUSES or run.status == "cancel_requested":
                raise WorkflowError("scenario_run_stopped")
            step = _required_step(session, run_id, node_id)
            if step.status == "succeeded":
                return step
            if step.attempt > attempt:
                raise WorkflowConflict("stale_step_attempt")
            step.status = "running"
            step.attempt = attempt
            step.input_ref = input_ref
            step.error = None
            step.started_at = step.started_at or utcnow()
            run.status = "running"
            append_run_event(
                session, run, "step.running", node_id=node_id, payload={"attempt": attempt}
            )
            session.commit()
            return step

    def finish_step(
        self,
        run_id: str,
        node_id: str,
        attempt: int,
        *,
        status: str = "succeeded",
        output_ref: str | None = None,
        diagnostics: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            step = _required_step(session, run_id, node_id)
            if step.attempt != attempt:
                raise WorkflowConflict("stale_step_attempt")
            if step.status in {"succeeded", "skipped", "cancelled"}:
                return
            step.status = status
            step.output_ref = output_ref or step.output_ref
            step.diagnostics = {**(step.diagnostics or {}), **(diagnostics or {})}
            step.error = error
            if status == "waiting":
                run.status = "waiting"
            else:
                step.completed_at = utcnow()
            append_run_event(
                session,
                run,
                f"step.{status}",
                node_id=node_id,
                payload={"attempt": attempt, "error": error},
            )
            session.commit()

    def finish_run(
        self, run_id: str, status: str, *, error: str | None = None, result_ref: str | None = None
    ) -> None:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                return
            if run.status == "cancel_requested" and status == "succeeded":
                status = "cancelled"
            run.status = status
            run.error = error
            run.result_ref = result_ref
            run.completed_at = utcnow()
            for step in session.scalars(
                select(ScenarioStepRunOrm).where(ScenarioStepRunOrm.run_id == run_id)
            ):
                if step.status in {"pending", "running", "waiting"}:
                    step.status = (
                        "cancelled" if status in {"cancelled", "rejected", "failed"} else "skipped"
                    )
                    step.completed_at = utcnow()
            append_run_event(session, run, f"run.{status}", payload={"error": error})
            session.commit()

    def update_diagnostics(
        self, run_id: str, node_id: str, attempt: int, values: dict[str, Any]
    ) -> None:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            step = _required_step(session, run_id, node_id)
            if step.attempt != attempt:
                raise WorkflowConflict("stale_step_attempt")
            step.diagnostics = {**(step.diagnostics or {}), **values}
            append_run_event(session, run, "step.diagnostics", node_id=node_id)
            session.commit()

    def pin_artifact(
        self, run_id: str, artifact_id: str, node_id: str | None, *, role: str = "output"
    ) -> None:
        with self.db.session() as session:
            run = _locked_run(session, run_id)
            artifact = session.scalar(
                select(FileArtifactOrm).where(FileArtifactOrm.id == artifact_id).with_for_update()
            )
            if (
                artifact is None
                or artifact.owner_user_id != run.owner_user_id
                or artifact.workspace_id != run.workspace_id
                or artifact.conversation_id != run.conversation_id
            ):
                raise WorkflowNotFound("scenario_artifact_not_found")
            if artifact.status not in {"ready", "staged"}:
                raise WorkflowConflict("artifact_unavailable")
            if session.get(ScenarioArtifactOrm, (run_id, artifact_id)) is None:
                session.add(
                    ScenarioArtifactOrm(
                        run_id=run_id, artifact_id=artifact_id, node_id=node_id, role=role
                    )
                )
            artifact.status = "ready"
            session.commit()

    def link_execution(self, run_id: str, node_id: str, execution_id: str) -> None:
        with self.db.session() as session:
            session.scalar(
                select(ScenarioWorkflowRunOrm)
                .where(ScenarioWorkflowRunOrm.id == run_id)
                .with_for_update()
            )
            if session.get(ScenarioExecutionOrm, (run_id, execution_id)) is None:
                session.add(
                    ScenarioExecutionOrm(run_id=run_id, node_id=node_id, execution_id=execution_id)
                )
                session.commit()

    def outbox(self) -> list[ScenarioOutboxOrm]:
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(ScenarioOutboxOrm)
                    .where(
                        ScenarioOutboxOrm.delivered.is_(False),
                    )
                    .order_by(ScenarioOutboxOrm.created_at, ScenarioOutboxOrm.id)
                    .limit(100)
                )
            )

    def acknowledge(self, outbox_id: str, error: str | None = None) -> None:
        with self.db.session() as session:
            row = session.get(ScenarioOutboxOrm, outbox_id)
            if row is None or row.delivered:
                return
            row.attempts += 1
            row.last_error = error
            row.delivered = error is None
            session.commit()
