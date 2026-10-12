from datetime import UTC, datetime

import pytest
from rhetorica_backend.contracts.coding import UpsertWorkspaceCodingDefaults
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.coding_defaults_repository import CodingDefaultsRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_models import (
    WorkspaceOrm,
    WorkspacePolicyAuditEventOrm,
)
from sqlalchemy import select


def test_workspace_settings_are_isolated_revisioned_and_audited(tmp_path):
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/defaults.db")
    db.create_schema()
    with db.session() as session:
        for workspace_id in ["one", "two"]:
            session.add(
                WorkspaceOrm(
                    id=workspace_id,
                    name=workspace_id,
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        session.commit()
    repo = CodingDefaultsRepository(db)
    assert repo.get("one").revision == 0
    request = UpsertWorkspaceCodingDefaults(expected_revision=0, resources=None)
    first = repo.replace(workspace_id="one", actor_user_id="owner", request=request)
    assert first.revision == 1
    assert repo.get("two").revision == 0
    with pytest.raises(CodingRunError, match="coding_defaults_revision_conflict"):
        repo.replace(workspace_id="one", actor_user_id="owner", request=request)
    with db.session() as session:
        audit = session.scalar(select(WorkspacePolicyAuditEventOrm))
        assert audit.target_type == "coding_defaults"
        assert audit.current_value["revision"] == 1
    with pytest.raises(CodingRunError, match="workspace_access_denied"):
        repo.replace(workspace_id="unknown", actor_user_id="owner", request=request)
