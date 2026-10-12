from __future__ import annotations

from pathlib import Path

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    GitLabConnectionConfig,
    UpsertGitLabConnectorRequest,
)
from rhetorica_backend.contracts.agents import UpsertAgentProfileRequest
from rhetorica_backend.contracts.coding import GitLabWriteScope, GovernedAgentSettings
from rhetorica_backend.contracts.workspaces import CreateWorkspaceRequest
from rhetorica_backend.infra.postgres.agent_policy_repository import (
    AgentPolicyConflictError,
    AgentPolicyRepository,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_policy_models import AgentGovernedPolicyOrm
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_models import WorkspacePolicyAuditEventOrm
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.secrets import InMemorySecretStore
from sqlalchemy import delete, select


class TrackingSecretStore(InMemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        self.reads += 1
        return super().get_secret(workspace_id, secret_key)


def _setup(tmp_path: Path):
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path / 'agent-policies.db'}")
    runtime.create_schema()
    users = UserAccountRepository(runtime)
    admin, _ = users.ensure_local_admin(
        workspace_id="platform", login="admin", password_hash="hashed"
    )
    workspaces = WorkspaceRepository(runtime)
    workspaces.ensure_home_membership(user_id=admin.id)
    workspaces.create(
        CreateWorkspaceRequest(id="analytics", name="Analytics"), actor_user_id=admin.id
    )
    secrets = TrackingSecretStore()
    connector = GitLabConnectorRepository(runtime, secret_store=secrets).upsert(
        UpsertGitLabConnectorRequest(
            name="gitlab-main",
            workspace_id="analytics",
            connection=GitLabConnectionConfig(
                base_url="https://gitlab.example.test",
                api_token="secret",
                use_when="Governed publication",
            ),
        )
    )
    profiles = AgentProfileRepository(runtime)
    parent = profiles.create(
        UpsertAgentProfileRequest(
            workspace_id="analytics",
            slug="parent",
            title="Parent",
            allowed_gitlab_connector_names=["gitlab-main"],
        ),
        actor_user_id=admin.id,
    )
    child = profiles.create(
        UpsertAgentProfileRequest(
            workspace_id="analytics",
            slug="child",
            title="Child",
            allowed_gitlab_connector_names=["gitlab-main"],
        ),
        actor_user_id=admin.id,
    )
    return runtime, admin, connector, parent, child, secrets


def test_policy_replace_is_versioned_audited_and_does_not_read_credentials(
    tmp_path: Path,
) -> None:
    runtime, admin, connector, parent, child, secrets = _setup(tmp_path)
    repository = AgentPolicyRepository(runtime)
    original = repository.get_agent_policy(workspace_id="analytics", agent_id=parent.id)
    assert original is not None
    assert original.gitlab_write_scopes == []
    reads_before = secrets.reads

    saved = repository.replace_agent_policy(
        workspace_id="analytics",
        agent_id=parent.id,
        actor_user_id=admin.id,
        settings=GovernedAgentSettings(
            revision=original.revision,
            gitlab_write_scopes=[
                GitLabWriteScope(
                    connector_id=connector.id,
                    project_id="data/platform",
                    target_branches=["main"],
                    allowed_paths=["packages/backend"],
                )
            ],
            allowed_subagent_ids=[child.id],
        ),
    )

    assert saved.revision == 2
    assert secrets.reads == reads_before
    with runtime.session() as session:
        event = session.execute(
            select(WorkspacePolicyAuditEventOrm).where(
                WorkspacePolicyAuditEventOrm.target_type == "agent_governed_policy"
            )
        ).scalar_one()
        assert event.target_id == parent.id
        assert event.previous_value is not None
        assert event.current_value is not None
        assert event.current_value["revision"] == 2


def test_stale_policy_revision_is_rejected_without_partial_write(tmp_path: Path) -> None:
    runtime, admin, connector, parent, _child, _secrets = _setup(tmp_path)
    repository = AgentPolicyRepository(runtime)
    first = GovernedAgentSettings(
        revision=1,
        gitlab_write_scopes=[
            GitLabWriteScope(
                connector_id=connector.id,
                project_id="data/platform",
                target_branches=["main"],
                allowed_paths=["src"],
            )
        ],
    )
    repository.replace_agent_policy(
        workspace_id="analytics",
        agent_id=parent.id,
        actor_user_id=admin.id,
        settings=first,
    )

    with pytest.raises(AgentPolicyConflictError) as caught:
        repository.replace_agent_policy(
            workspace_id="analytics",
            agent_id=parent.id,
            actor_user_id=admin.id,
            settings=first.model_copy(update={"max_iterations": 7}),
        )

    assert caught.value.current_revision == 2
    current = repository.get_agent_policy(workspace_id="analytics", agent_id=parent.id)
    assert current is not None
    assert current.revision == 2
    assert current.max_iterations == 100


def test_missing_row_reads_closed_and_is_materialized_on_update(tmp_path: Path) -> None:
    runtime, admin, _connector, parent, _child, _secrets = _setup(tmp_path)
    with runtime.session() as session:
        session.execute(
            delete(AgentGovernedPolicyOrm).where(AgentGovernedPolicyOrm.agent_id == parent.id)
        )
        session.commit()
    repository = AgentPolicyRepository(runtime)

    closed = repository.get_agent_policy(workspace_id="analytics", agent_id=parent.id)
    assert closed is not None
    assert closed.revision == 1
    assert closed.gitlab_write_scopes == []

    saved = repository.replace_agent_policy(
        workspace_id="analytics",
        agent_id=parent.id,
        actor_user_id=admin.id,
        settings=GovernedAgentSettings(revision=1, max_iterations=9),
    )
    assert saved.revision == 2
    assert saved.max_iterations == 9


def test_audit_failure_rolls_back_policy_and_revision(tmp_path: Path, monkeypatch) -> None:
    runtime, admin, _connector, parent, _child, _secrets = _setup(tmp_path)
    repository = AgentPolicyRepository(runtime)

    def fail_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(repository, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.replace_agent_policy(
            workspace_id="analytics",
            agent_id=parent.id,
            actor_user_id=admin.id,
            settings=GovernedAgentSettings(revision=1, max_iterations=9),
        )

    current = AgentPolicyRepository(runtime).get_agent_policy(
        workspace_id="analytics", agent_id=parent.id
    )
    assert current is not None
    assert current.revision == 1
    assert current.max_iterations == 100
