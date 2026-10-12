"""Authoring transactions: ownership, optimistic drafts and immutable versions."""

from __future__ import annotations

from typing import Literal, cast
from uuid import uuid4

from rhetorica_backend.contracts.scenario_workflows import (
    SaveScenarioDraftRequest,
    ScenarioDraft,
    ScenarioLayout,
    ScenarioTestCase,
    ScenarioVersion,
    ScenarioWorkflowDefinition,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowConflict, WorkflowNotFound
from rhetorica_backend.domain.scenarios.legacy import legacy_snapshot, legacy_workflow
from rhetorica_backend.infra.postgres.scenario_models import ScenarioDefinitionOrm
from rhetorica_backend.infra.postgres.scenario_workflow_models import (
    ScenarioDraftOrm,
    ScenarioVersionOrm,
    utcnow,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def accessible_definition(
    session: Session,
    scenario_id: str,
    user: UserAccountItem,
    *,
    edit: bool = False,
    lock: bool = False,
) -> ScenarioDefinitionOrm:
    query = select(ScenarioDefinitionOrm).where(
        ScenarioDefinitionOrm.id == scenario_id,
        ScenarioDefinitionOrm.workspace_id == user.workspace_id,
    )
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None or not user.is_active:
        raise WorkflowNotFound()
    if row.scope == "personal" and row.owner_user_id != user.id:
        raise WorkflowNotFound()
    if row.scope == "official" and (
        (edit and not user.is_admin) or (row.status != "published" and not user.is_admin)
    ):
        raise WorkflowNotFound()
    return row


def version_contract(row: ScenarioVersionOrm) -> ScenarioVersion:
    workflow = (
        legacy_workflow(row.definition)
        if row.kind == "legacy"
        else ScenarioWorkflowDefinition.model_validate(row.definition)
    )
    return ScenarioVersion(
        id=row.id,
        scenario_id=row.scenario_id,
        version=row.version,
        kind=cast(Literal["legacy", "workflow"], row.kind),
        workflow=workflow,
        created_at=row.created_at,
        created_by=row.created_by,
    )


class ScenarioAuthoringRepository:
    def __init__(self, db: DatabaseRuntime):
        self.db = db

    def get_draft(self, scenario_id: str, user: UserAccountItem) -> ScenarioDraft:
        with self.db.session() as session:
            definition = accessible_definition(session, scenario_id, user, edit=True)
            draft = session.get(ScenarioDraftOrm, scenario_id)
            if draft:
                return self._draft(draft)
            version = session.scalar(
                select(ScenarioVersionOrm)
                .where(
                    ScenarioVersionOrm.scenario_id == scenario_id,
                    ScenarioVersionOrm.kind == "workflow",
                )
                .order_by(ScenarioVersionOrm.version.desc())
            )
            workflow = (
                version_contract(version).workflow
                if version
                else legacy_workflow(legacy_snapshot(definition))
            )
            return ScenarioDraft(scenario_id=scenario_id, revision=0, workflow=workflow)

    def save_draft(
        self,
        scenario_id: str,
        user: UserAccountItem,
        request: SaveScenarioDraftRequest,
    ) -> ScenarioDraft:
        with self.db.session() as session:
            accessible_definition(session, scenario_id, user, edit=True, lock=True)
            values = {
                "workflow": request.workflow.model_dump(mode="json"),
                "layout": request.layout.model_dump(mode="json"),
                "test_cases": [case.model_dump(mode="json") for case in request.test_cases],
                "revision": request.expected_revision + 1,
                "updated_at": utcnow(),
                "updated_by": user.id,
            }
            if request.expected_revision == 0:
                session.add(ScenarioDraftOrm(scenario_id=scenario_id, **values))
            else:
                changed = session.execute(
                    update(ScenarioDraftOrm)
                    .where(
                        ScenarioDraftOrm.scenario_id == scenario_id,
                        ScenarioDraftOrm.revision == request.expected_revision,
                    )
                    .values(**values)
                )
                if cast(CursorResult, changed).rowcount != 1:
                    raise WorkflowConflict()
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise WorkflowConflict() from exc
            row = session.get(ScenarioDraftOrm, scenario_id)
            if row is None:
                raise WorkflowNotFound()
            return self._draft(row)

    def publish(self, scenario_id: str, user: UserAccountItem, revision: int) -> ScenarioVersion:
        with self.db.session() as session:
            definition = accessible_definition(session, scenario_id, user, edit=True, lock=True)
            draft = session.get(ScenarioDraftOrm, scenario_id)
            if draft is None or draft.revision != revision:
                raise WorkflowConflict()
            self.ensure_legacy_version(session, definition)
            latest = (
                session.scalar(
                    select(func.max(ScenarioVersionOrm.version)).where(
                        ScenarioVersionOrm.scenario_id == scenario_id
                    )
                )
                or 0
            )
            row = ScenarioVersionOrm(
                id=str(uuid4()),
                scenario_id=scenario_id,
                version=max(latest, definition.version) + 1,
                kind="workflow",
                definition=draft.workflow,
                created_at=utcnow(),
                created_by=user.id,
            )
            session.add(row)
            definition.version = row.version
            parameters = draft.workflow.get("parameters", {})
            definition.input_schema = {
                "type": "object",
                "properties": {
                    name: {
                        "type": {
                            "file": "object",
                            "files": "array",
                            "table": "object",
                            "json": "object",
                        }.get(spec["type"], spec["type"]),
                        "title": spec.get("title") or name,
                        "default": spec.get("default"),
                        "x-workflow-type": spec["type"],
                    }
                    for name, spec in parameters.items()
                },
                "required": [
                    name for name, spec in parameters.items() if spec.get("required", True)
                ],
            }
            definition.status = "published"
            definition.published_at = utcnow()
            definition.updated_at = utcnow()
            session.commit()
            return version_contract(row)

    def list_versions(self, scenario_id: str, user: UserAccountItem) -> list[ScenarioVersion]:
        with self.db.session() as session:
            definition = accessible_definition(session, scenario_id, user, lock=True)
            self.ensure_legacy_version(session, definition)
            rows = list(
                session.scalars(
                    select(ScenarioVersionOrm)
                    .where(
                        ScenarioVersionOrm.scenario_id == scenario_id,
                    )
                    .order_by(ScenarioVersionOrm.version.desc())
                )
            )
            session.commit()
            return [version_contract(row) for row in rows]

    def has_workflow(self, scenario_id: str) -> bool:
        with self.db.session() as session:
            return (
                session.scalar(
                    select(ScenarioVersionOrm.id)
                    .where(
                        ScenarioVersionOrm.scenario_id == scenario_id,
                        ScenarioVersionOrm.kind == "workflow",
                    )
                    .limit(1)
                )
                is not None
            )

    @staticmethod
    def ensure_legacy_version(session: Session, definition: ScenarioDefinitionOrm) -> None:
        current = session.scalar(
            select(ScenarioVersionOrm.id).where(
                ScenarioVersionOrm.scenario_id == definition.id,
                ScenarioVersionOrm.version == definition.version,
            )
        )
        workflow = session.scalar(
            select(ScenarioVersionOrm.id)
            .where(
                ScenarioVersionOrm.scenario_id == definition.id,
                ScenarioVersionOrm.kind == "workflow",
            )
            .limit(1)
        )
        if current is None and workflow is None:
            session.add(
                ScenarioVersionOrm(
                    id=str(uuid4()),
                    scenario_id=definition.id,
                    version=definition.version,
                    kind="legacy",
                    definition=legacy_snapshot(definition),
                    created_at=definition.created_at,
                    created_by=definition.owner_user_id,
                )
            )
            session.flush()

    @staticmethod
    def _draft(row: ScenarioDraftOrm) -> ScenarioDraft:
        return ScenarioDraft(
            scenario_id=row.scenario_id,
            revision=row.revision,
            workflow=ScenarioWorkflowDefinition.model_validate(row.workflow),
            layout=ScenarioLayout.model_validate(row.layout),
            test_cases=[ScenarioTestCase.model_validate(case) for case in row.test_cases],
            updated_at=row.updated_at,
        )
