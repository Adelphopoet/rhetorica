"""Durable run admission and owner-scoped reads."""

from __future__ import annotations

from typing import cast
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.scenario_workflows import (
    TERMINAL_RUN_STATUSES,
    RunStatus,
    ScenarioRun,
    ScenarioRunEvent,
    ScenarioStepRun,
    ScenarioWorkflowDefinition,
    StartScenarioRunRequest,
    StepStatus,
)
from rhetorica_backend.domain.scenarios.errors import (
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
)
from rhetorica_backend.domain.scenarios.legacy import legacy_workflow
from rhetorica_backend.infra.postgres.file_models import FileArtifactOrm
from rhetorica_backend.infra.postgres.scenario_authoring_repository import (
    ScenarioAuthoringRepository,
    accessible_definition,
)
from rhetorica_backend.infra.postgres.scenario_workflow_models import (
    ScenarioArtifactOrm,
    ScenarioDraftOrm,
    ScenarioOutboxOrm,
    ScenarioRunEventOrm,
    ScenarioStepRunOrm,
    ScenarioVersionOrm,
    ScenarioWorkflowRunOrm,
    utcnow,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def append_run_event(
    session: Session,
    run: ScenarioWorkflowRunOrm,
    event_type: str,
    *,
    node_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Caller locks the run row, serializing event cursors with state changes."""
    run.event_sequence += 1
    session.add(
        ScenarioRunEventOrm(
            run_id=run.id,
            sequence=run.event_sequence,
            type=event_type,
            node_id=node_id,
            payload=payload or {},
            created_at=utcnow(),
        )
    )


def owned_run(
    session: Session, run_id: str, user: UserAccountItem, *, lock: bool = False
) -> ScenarioWorkflowRunOrm:
    query = select(ScenarioWorkflowRunOrm).where(
        ScenarioWorkflowRunOrm.id == run_id,
        ScenarioWorkflowRunOrm.owner_user_id == user.id,
        ScenarioWorkflowRunOrm.workspace_id == user.workspace_id,
    )
    run = session.scalar(query.with_for_update() if lock else query)
    if run is None or not user.is_active:
        raise WorkflowNotFound("scenario_run_not_found")
    return run


def step_contract(row: ScenarioStepRunOrm) -> ScenarioStepRun:
    return ScenarioStepRun(
        node_id=row.node_id,
        status=cast(StepStatus, row.status),
        attempt=row.attempt,
        operation_id=row.operation_id,
        input_ref=row.input_ref,
        output_ref=row.output_ref,
        diagnostics=row.diagnostics or {},
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def run_contract(
    row: ScenarioWorkflowRunOrm, steps: list[ScenarioStepRunOrm] | None = None
) -> ScenarioRun:
    return ScenarioRun(
        id=row.id,
        scenario_id=row.scenario_id,
        scenario_title=row.scenario_title,
        version=row.version,
        status=cast(RunStatus, row.status),
        conversation_id=row.conversation_id,
        is_test=row.is_test,
        created_at=row.created_at,
        completed_at=row.completed_at,
        error=row.error,
        steps=[step_contract(step) for step in steps or []],
    )


class ScenarioRunRepository:
    def __init__(self, db: DatabaseRuntime):
        self.db = db

    def find_request(
        self, user: UserAccountItem, key: str, request_hash: str
    ) -> ScenarioRun | None:
        with self.db.session() as session:
            run = session.scalar(
                select(ScenarioWorkflowRunOrm).where(
                    ScenarioWorkflowRunOrm.owner_user_id == user.id,
                    ScenarioWorkflowRunOrm.idempotency_key == key,
                )
            )
            if run is None:
                return None
            if run.request_hash != request_hash or run.workspace_id != user.workspace_id:
                raise WorkflowConflict("idempotency_key_reused")
            return run_contract(run)

    def resolve_definition(
        self,
        scenario_id: str,
        user: UserAccountItem,
        request: StartScenarioRunRequest,
    ) -> tuple[int, str, dict, ScenarioWorkflowDefinition, str]:
        with self.db.session() as session:
            definition = accessible_definition(
                session, scenario_id, user, edit=request.draft_revision is not None, lock=True
            )
            if definition.status == "archived":
                raise WorkflowError("scenario_archived")
            if request.draft_revision is not None:
                draft = session.get(ScenarioDraftOrm, scenario_id)
                if draft is None or draft.revision != request.draft_revision:
                    raise WorkflowConflict()
                return (
                    definition.version,
                    "workflow",
                    draft.workflow,
                    ScenarioWorkflowDefinition.model_validate(draft.workflow),
                    definition.title,
                )
            if definition.status != "published":
                raise WorkflowError("scenario_not_published")
            ScenarioAuthoringRepository.ensure_legacy_version(session, definition)
            query = select(ScenarioVersionOrm).where(ScenarioVersionOrm.scenario_id == scenario_id)
            if request.version is not None:
                query = query.where(ScenarioVersionOrm.version == request.version)
            row = session.scalar(query.order_by(ScenarioVersionOrm.version.desc()).limit(1))
            if row is None:
                raise WorkflowNotFound("scenario_version_not_found")
            workflow = (
                legacy_workflow(row.definition)
                if row.kind == "legacy"
                else ScenarioWorkflowDefinition.model_validate(row.definition)
            )
            session.commit()
            return row.version, row.kind, row.definition, workflow, definition.title

    def create(
        self,
        *,
        run_id: str,
        scenario_id: str,
        title: str,
        user: UserAccountItem,
        request: StartScenarioRunRequest,
        key: str,
        request_hash: str,
        version: int,
        kind: str,
        definition: dict,
        workflow: ScenarioWorkflowDefinition,
        agent_snapshots: dict,
        conversation_id: str,
        inputs: dict,
        artifact_ids: list[str] | None = None,
    ) -> ScenarioRun:
        with self.db.session() as session, session.no_autoflush:
            run = ScenarioWorkflowRunOrm(
                id=run_id,
                scenario_id=scenario_id,
                scenario_title=title,
                workspace_id=user.workspace_id,
                owner_user_id=user.id,
                version=version,
                kind=kind,
                definition=definition,
                agent_snapshots=agent_snapshots,
                inputs=inputs,
                idempotency_key=key,
                request_hash=request_hash,
                conversation_id=conversation_id,
                status="queued",
                is_test=request.draft_revision is not None,
                event_sequence=0,
                created_at=utcnow(),
                test_config={
                    "node_id": request.test_node_id,
                    "inputs": request.test_inputs,
                    "expected_schema": request.expected_schema,
                },
            )
            session.add(run)
            # Independent child mappers need a persisted parent, in the same transaction.
            try:
                session.flush([run])
            except IntegrityError:
                session.rollback()
                duplicate = self.find_request(user, key, request_hash)
                if duplicate is not None:
                    return duplicate
                raise
            for artifact_id in artifact_ids or []:
                artifact = session.scalar(
                    select(FileArtifactOrm)
                    .where(FileArtifactOrm.id == artifact_id)
                    .with_for_update()
                )
                if (
                    artifact is None
                    or artifact.owner_user_id != user.id
                    or artifact.workspace_id != user.workspace_id
                    or artifact.conversation_id != conversation_id
                    or artifact.status not in {"staged", "ready"}
                ):
                    raise WorkflowNotFound("scenario_artifact_not_found")
                artifact.status = "ready"
                session.add(
                    ScenarioArtifactOrm(
                        run_id=run_id, artifact_id=artifact_id, node_id=None, role="input"
                    )
                )
            for node in workflow.nodes:
                session.add(
                    ScenarioStepRunOrm(
                        run_id=run_id,
                        node_id=node.id,
                        operation_id=str(
                            uuid5(NAMESPACE_URL, f"rhetorica/scenario/{run_id}/{node.id}")
                        ),
                        status="pending",
                        attempt=0,
                    )
                )
            append_run_event(session, run, "run.queued")
            session.add(
                ScenarioOutboxOrm(id=f"start:{run_id}", run_id=run_id, kind="start", payload={})
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                duplicate = self.find_request(user, key, request_hash)
                if duplicate is not None:
                    return duplicate
                raise
            return run_contract(run)

    def coding_node_ids(self, run_id: str, user: UserAccountItem) -> list[str]:
        with self.db.session() as session:
            run = owned_run(session, run_id, user)
            return [str(node["id"]) for node in run.definition.get("nodes", []) if node.get("type") == "coding_task"]

    def get(self, run_id: str, user: UserAccountItem) -> tuple[ScenarioRun, str | None]:
        with self.db.session() as session:
            run = owned_run(session, run_id, user)
            steps = list(
                session.scalars(
                    select(ScenarioStepRunOrm).where(ScenarioStepRunOrm.run_id == run_id)
                )
            )
            return run_contract(run, steps), run.result_ref

    def list_runs(
        self,
        user: UserAccountItem,
        *,
        scenario_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[ScenarioRun]:
        with self.db.session() as session:
            query = select(ScenarioWorkflowRunOrm).where(
                ScenarioWorkflowRunOrm.workspace_id == user.workspace_id,
                ScenarioWorkflowRunOrm.owner_user_id == user.id,
            )
            if scenario_id:
                query = query.where(ScenarioWorkflowRunOrm.scenario_id == scenario_id)
            if conversation_id:
                query = query.where(ScenarioWorkflowRunOrm.conversation_id == conversation_id)
            return [
                run_contract(row)
                for row in session.scalars(
                    query.order_by(ScenarioWorkflowRunOrm.created_at.desc()).limit(100)
                )
            ]

    def events(self, run_id: str, user: UserAccountItem, after: int = 0) -> list[ScenarioRunEvent]:
        with self.db.session() as session:
            owned_run(session, run_id, user)
            rows = session.scalars(
                select(ScenarioRunEventOrm)
                .where(
                    ScenarioRunEventOrm.run_id == run_id,
                    ScenarioRunEventOrm.sequence > after,
                )
                .order_by(ScenarioRunEventOrm.sequence)
                .limit(200)
            )
            return [
                ScenarioRunEvent(
                    run_id=row.run_id,
                    sequence=row.sequence,
                    type=row.type,
                    node_id=row.node_id,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def cancel(self, run_id: str, user: UserAccountItem) -> ScenarioRun:
        with self.db.session() as session:
            run = owned_run(session, run_id, user, lock=True)
            if run.status not in TERMINAL_RUN_STATUSES and run.status != "cancel_requested":
                run.status = "cancel_requested"
                append_run_event(session, run, "run.cancel_requested")
                session.add(
                    ScenarioOutboxOrm(
                        id=f"cancel:{run_id}", run_id=run_id, kind="cancel", payload={}
                    )
                )
                session.commit()
            return run_contract(run)

    def approve(self, run_id: str, node_id: str, user: UserAccountItem, approved: bool) -> None:
        with self.db.session() as session:
            run = owned_run(session, run_id, user, lock=True)
            step = session.get(ScenarioStepRunOrm, (run_id, node_id))
            if step is None:
                raise WorkflowNotFound("scenario_step_not_found")
            if step.decision is not None:
                if step.decision != approved:
                    raise WorkflowConflict("approval_already_decided")
                return
            if (
                step.status != "waiting"
                or run.status in TERMINAL_RUN_STATUSES
                or run.status == "cancel_requested"
            ):
                raise WorkflowConflict("step_not_awaiting_approval")
            step.decision = approved
            append_run_event(
                session, run, "step.approved" if approved else "step.rejected", node_id=node_id
            )
            session.add(
                ScenarioOutboxOrm(
                    id=f"approve:{run_id}:{node_id}",
                    run_id=run_id,
                    kind="approve",
                    payload={"node_id": node_id, "approved": approved},
                )
            )
            session.commit()
