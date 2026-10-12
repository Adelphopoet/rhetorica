"""Workspace-owned coding presets with revision conflicts and policy audit."""

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.coding import (
    UpsertWorkspaceCodingDefaults,
    WorkspaceCodingDefaults,
)
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.coding_sandbox_models import WorkspaceCodingDefaultsOrm
from rhetorica_backend.infra.postgres.workspace_models import (
    WorkspaceOrm,
    WorkspacePolicyAuditEventOrm,
)
from sqlalchemy import select


class CodingDefaultsRepository:
    def __init__(self, db):
        self._db = db

    def get(self, workspace_id: str) -> WorkspaceCodingDefaults:
        with self._db.session() as session:
            row = session.get(WorkspaceCodingDefaultsOrm, workspace_id)
            return self._contract(workspace_id, row)

    def replace(
        self, *, workspace_id: str, actor_user_id: str, request: UpsertWorkspaceCodingDefaults
    ):
        with self._db.session() as session:
            workspace = session.scalar(
                select(WorkspaceOrm)
                .where(WorkspaceOrm.id == workspace_id, WorkspaceOrm.is_active.is_(True))
                .with_for_update(key_share=True)
            )
            if workspace is None:
                raise CodingRunError("workspace_access_denied", status_code=403)
            row = session.get(WorkspaceCodingDefaultsOrm, workspace_id)
            previous = self._contract(workspace_id, row)
            if previous.revision != request.expected_revision:
                raise CodingRunError("coding_defaults_revision_conflict", status_code=409)
            now = datetime.now(UTC)
            if row is None:
                row = WorkspaceCodingDefaultsOrm(workspace_id=workspace_id)
                session.add(row)
            row.revision = previous.revision + 1
            row.resources_json = (
                request.resources.model_dump(mode="json") if request.resources else None
            )
            row.updated_at, row.updated_by = now, actor_user_id
            current = self._contract(workspace_id, row)
            session.add(
                WorkspacePolicyAuditEventOrm(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    target_type="coding_defaults",
                    target_id=workspace_id,
                    action="replace",
                    previous_value=previous.model_dump(mode="json"),
                    current_value=current.model_dump(mode="json"),
                    created_at=now,
                )
            )
            session.commit()
            return current

    @staticmethod
    def _contract(workspace_id, row):
        return WorkspaceCodingDefaults(
            workspace_id=workspace_id,
            revision=row.revision if row else 0,
            resources=row.resources_json if row else None,
        )
