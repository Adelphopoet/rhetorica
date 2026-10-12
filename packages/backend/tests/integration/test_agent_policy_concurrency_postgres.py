"""Real PostgreSQL lock/CAS coverage for governed agent policy updates."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Barrier
from uuid import uuid4

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    GitLabConnectionConfig,
    UpsertGitLabConnectorRequest,
)
from rhetorica_backend.contracts.agents import UpsertAgentProfileRequest
from rhetorica_backend.contracts.coding import GovernedAgentSettings
from rhetorica_backend.contracts.workspaces import (
    CreateWorkspaceRequest,
    CreateWorkspaceResourceGrantRequest,
)
from rhetorica_backend.domain.coding.policies import AgentPolicyValidationError
from rhetorica_backend.infra.postgres.agent_policy_repository import (
    AgentPolicyConflictError,
    AgentPolicyRepository,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_models import WorkspaceOrm
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.secrets import InMemorySecretStore
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker


def _runtime():
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL policy concurrency coverage")
    schema = f"agent_policy_lock_{uuid4().hex}"
    root = DatabaseRuntime(database_url)
    with root.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = root.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(engine)
    return root, runtime, schema


def _seed(runtime: DatabaseRuntime):
    users = UserAccountRepository(runtime)
    admin, _ = users.ensure_local_admin(
        workspace_id="platform", login="admin", password_hash="hashed"
    )
    workspaces = WorkspaceRepository(runtime)
    workspaces.ensure_home_membership(user_id=admin.id)
    workspaces.create(
        CreateWorkspaceRequest(id="analytics", name="Analytics"), actor_user_id=admin.id
    )
    profiles = AgentProfileRepository(runtime)
    first = profiles.create(
        UpsertAgentProfileRequest(workspace_id="analytics", slug="first", title="First"),
        actor_user_id=admin.id,
    )
    second = profiles.create(
        UpsertAgentProfileRequest(workspace_id="analytics", slug="second", title="Second"),
        actor_user_id=admin.id,
    )
    return admin, first, second


def _cleanup(root: DatabaseRuntime, runtime: DatabaseRuntime, schema: str) -> None:
    runtime.engine.dispose()
    with root.engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    root.engine.dispose()


def test_same_revision_has_one_success_and_one_conflict_without_deadlock() -> None:
    root, runtime, schema = _runtime()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        admin, first, _second = _seed(runtime)
        start = Barrier(3)

        def update(limit: int) -> str:
            start.wait()
            try:
                AgentPolicyRepository(runtime).replace_agent_policy(
                    workspace_id="analytics",
                    agent_id=first.id,
                    actor_user_id=admin.id,
                    settings=GovernedAgentSettings(revision=1, max_iterations=limit),
                )
            except AgentPolicyConflictError:
                return "conflict"
            return "saved"

        futures = [pool.submit(update, 10), pool.submit(update, 20)]
        start.wait()
        assert sorted(future.result(timeout=10) for future in futures) == ["conflict", "saved"]
        current = AgentPolicyRepository(runtime).get_agent_policy(
            workspace_id="analytics", agent_id=first.id
        )
        assert current is not None
        assert current.revision == 2
        assert current.max_iterations in {10, 20}
    finally:
        pool.shutdown(wait=True)
        _cleanup(root, runtime, schema)


def test_concurrent_opposite_edges_cannot_create_a_cycle() -> None:
    root, runtime, schema = _runtime()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        admin, first, second = _seed(runtime)
        start = Barrier(3)

        def allow(parent_id: str, child_id: str) -> str:
            start.wait()
            try:
                AgentPolicyRepository(runtime).replace_agent_policy(
                    workspace_id="analytics",
                    agent_id=parent_id,
                    actor_user_id=admin.id,
                    settings=GovernedAgentSettings(revision=1, allowed_subagent_ids=[child_id]),
                )
            except AgentPolicyValidationError:
                return "cycle_rejected"
            return "saved"

        futures = [
            pool.submit(allow, first.id, second.id),
            pool.submit(allow, second.id, first.id),
        ]
        start.wait()
        assert sorted(future.result(timeout=10) for future in futures) == [
            "cycle_rejected",
            "saved",
        ]
    finally:
        pool.shutdown(wait=True)
        _cleanup(root, runtime, schema)


def test_related_delete_deactivate_and_revoke_use_the_same_lock_order() -> None:
    root, runtime, schema = _runtime()
    pool = ThreadPoolExecutor(max_workers=3)
    lock_session = runtime.session()
    try:
        admin, first, second = _seed(runtime)
        workspaces = WorkspaceRepository(runtime)
        connector = GitLabConnectorRepository(runtime, secret_store=InMemorySecretStore()).upsert(
            UpsertGitLabConnectorRequest(
                name="shared-gitlab",
                workspace_id="platform",
                connection=GitLabConnectionConfig(
                    base_url="https://gitlab.example.test",
                    use_when="Concurrency test",
                ),
            )
        )
        grant = workspaces.create_resource_grant(
            CreateWorkspaceResourceGrantRequest(
                resource_kind="gitlab_connector",
                resource_id=connector.id,
                owner_workspace_id="platform",
                recipient_workspace_id="analytics",
                alias="shared-gitlab",
            ),
            actor_user_id=admin.id,
        )
        lock_session.execute(
            select(WorkspaceOrm).where(WorkspaceOrm.id == "analytics").with_for_update()
        ).scalar_one()
        start = Barrier(4)
        profiles = AgentProfileRepository(runtime)

        def delete_agent() -> bool:
            start.wait()
            return profiles.delete(agent_id=first.id, workspace_id="analytics")

        def deactivate_agent() -> bool:
            start.wait()
            return (
                profiles.update(
                    agent_id=second.id,
                    workspace_id="analytics",
                    request=UpsertAgentProfileRequest(
                        workspace_id="analytics",
                        slug=second.slug,
                        title=second.title,
                        is_active=False,
                    ),
                    actor_user_id=admin.id,
                )
                is not None
            )

        def revoke_resource() -> bool:
            start.wait()
            return workspaces.revoke_resource_grant(
                grant_id=grant.id,
                workspace_id="analytics",
                actor_user_id=admin.id,
            )

        futures = [
            pool.submit(delete_agent),
            pool.submit(deactivate_agent),
            pool.submit(revoke_resource),
        ]
        start.wait()
        completed_while_locked, _ = wait(futures, timeout=0.5)
        assert not completed_while_locked
        lock_session.commit()
        assert [future.result(timeout=10) for future in futures] == [True, True, True]
    finally:
        lock_session.rollback()
        lock_session.close()
        pool.shutdown(wait=True)
        _cleanup(root, runtime, schema)


def test_postgres_audit_failure_rolls_back_policy_revision(monkeypatch) -> None:
    root, runtime, schema = _runtime()
    try:
        admin, first, _second = _seed(runtime)
        repository = AgentPolicyRepository(runtime)

        def fail_audit(*_args, **_kwargs) -> None:
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(repository, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            repository.replace_agent_policy(
                workspace_id="analytics",
                agent_id=first.id,
                actor_user_id=admin.id,
                settings=GovernedAgentSettings(revision=1, max_iterations=9),
            )
        current = AgentPolicyRepository(runtime).get_agent_policy(
            workspace_id="analytics", agent_id=first.id
        )
        assert current is not None
        assert current.revision == 1
        assert current.max_iterations == 100
    finally:
        _cleanup(root, runtime, schema)
