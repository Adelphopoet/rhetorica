"""PostgreSQL coverage for workspace resource and artifact isolation."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Barrier
from uuid import uuid4

import pytest
from rhetorica_backend.contracts.connectors import UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.workspaces import (
    CreateWorkspaceRequest,
    CreateWorkspaceResourceGrantRequest,
    UpsertWorkspaceMembershipRequest,
)
from rhetorica_backend.domain.files.models import ArtifactSource, ArtifactStatus
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_models import WorkspaceMembershipOrm, WorkspaceOrm
from rhetorica_backend.infra.postgres.workspace_repository import (
    WorkspaceConflictError,
    WorkspaceRepository,
)
from rhetorica_backend.infra.secrets import InMemorySecretStore
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker


def test_workspace_aliases_resources_and_artifacts_are_isolated() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL workspace coverage")

    schema = f"workspace_isolation_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        Base.metadata.create_all(engine)
        users = UserAccountRepository(runtime)
        admin, _ = users.ensure_local_admin(
            workspace_id="platform",
            login="admin",
            password_hash="hashed",
        )
        workspaces = WorkspaceRepository(runtime)
        workspaces.ensure_home_membership(user_id=admin.id)
        for workspace_id in ("analytics", "science"):
            workspaces.create(
                CreateWorkspaceRequest(id=workspace_id, name=workspace_id.title()),
                actor_user_id=admin.id,
            )

        secrets = InMemorySecretStore()
        connectors = ConnectorRepository(runtime, secret_store=secrets)
        analytics_connector = connectors.upsert(
            UpsertConnectorRequest(
                name="lake",
                workspace_id="analytics",
                connection=EngineConnectionConfig(
                    engine="trino",
                    host="analytics-trino",
                    port=8443,
                    username="analytics",
                    password="analytics-secret",
                ),
            )
        )
        science_connector = connectors.upsert(
            UpsertConnectorRequest(
                name="lake",
                workspace_id="science",
                connection=EngineConnectionConfig(
                    engine="trino",
                    host="science-trino",
                    port=8443,
                    username="science",
                    password="science-secret",
                ),
            )
        )

        assert connectors.get_connection("analytics", "lake").host == "analytics-trino"
        assert connectors.get_connection("science", "lake").host == "science-trino"
        assert connectors.get("science", analytics_connector.id) is None
        assert connectors.get("analytics", science_connector.id) is None

        grant = workspaces.create_resource_grant(
            CreateWorkspaceResourceGrantRequest(
                resource_kind="database_connector",
                resource_id=analytics_connector.id,
                owner_workspace_id="analytics",
                recipient_workspace_id="science",
                alias="analytics-lake",
            ),
            actor_user_id=admin.id,
        )
        assert connectors.get_connection("science", "analytics-lake").host == "analytics-trino"
        assert workspaces.revoke_resource_grant(
            grant_id=grant.id,
            workspace_id="science",
            actor_user_id=admin.id,
        )
        assert connectors.get_connection("science", "analytics-lake") is None

        conversation = ChatRepository(runtime).create_conversation(
            workspace_id="analytics",
            owner_user_id=admin.id,
        )
        files = FileArtifactRepository(runtime)
        artifact = files.create(
            artifact_id=str(uuid4()),
            workspace_id="analytics",
            owner_user_id=admin.id,
            conversation_id=conversation.id,
            storage_key="analytics/object",
            filename="result.csv",
            media_type="text/csv",
            size_bytes=1,
            sha256="0" * 64,
            source=ArtifactSource.SQL_RESULT,
            status=ArtifactStatus.READY,
        )
        assert files.get_owned(artifact.id, admin.id, "analytics") is not None
        assert files.get_owned(artifact.id, admin.id, "science") is None
    finally:
        runtime.engine.dispose()
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        root_runtime.engine.dispose()


def test_concurrent_owner_changes_preserve_one_active_owner() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL workspace coverage")

    schema = f"workspace_owner_lock_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    lock_session = runtime.session()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        Base.metadata.create_all(engine)
        users = UserAccountRepository(runtime)
        first_owner, _ = users.ensure_local_admin(
            workspace_id="platform",
            login="first-owner",
            password_hash="hashed",
        )
        second_owner = users.create_local_user(
            workspace_id="platform",
            login="second-owner",
            password_hash="hashed",
            role="analyst",
        )
        workspaces = WorkspaceRepository(runtime)
        workspaces.create(
            CreateWorkspaceRequest(id="analytics", name="Analytics"),
            actor_user_id=first_owner.id,
        )
        workspaces.upsert_membership(
            workspace_id="analytics",
            request=UpsertWorkspaceMembershipRequest(user_id=second_owner.id, role="owner"),
            actor_user_id=first_owner.id,
        )

        lock_session.execute(
            select(WorkspaceOrm).where(WorkspaceOrm.id == "analytics").with_for_update()
        ).scalar_one()
        start = Barrier(3)

        def revoke_first_owner() -> str:
            start.wait()
            try:
                workspaces.revoke_membership(
                    workspace_id="analytics",
                    user_id=first_owner.id,
                    actor_user_id=first_owner.id,
                )
            except WorkspaceConflictError as exc:
                return str(exc)
            else:
                return "changed"

        def downgrade_second_owner() -> str:
            start.wait()
            try:
                workspaces.upsert_membership(
                    workspace_id="analytics",
                    request=UpsertWorkspaceMembershipRequest(
                        user_id=second_owner.id,
                        role="analyst",
                    ),
                    actor_user_id=first_owner.id,
                )
            except WorkspaceConflictError as exc:
                return str(exc)
            else:
                return "changed"

        futures = [pool.submit(revoke_first_owner), pool.submit(downgrade_second_owner)]
        start.wait()
        completed_while_locked, _ = wait(futures, timeout=1)
        assert not completed_while_locked

        lock_session.commit()
        outcomes = sorted(future.result(timeout=5) for future in futures)
        assert outcomes == ["changed", "last_workspace_owner_required"]

        with runtime.session() as session:
            active_owners = (
                session.execute(
                    select(WorkspaceMembershipOrm).where(
                        WorkspaceMembershipOrm.workspace_id == "analytics",
                        WorkspaceMembershipOrm.role == "owner",
                        WorkspaceMembershipOrm.is_active.is_(True),
                    )
                )
                .scalars()
                .all()
            )
        assert len(active_owners) == 1
    finally:
        lock_session.rollback()
        lock_session.close()
        pool.shutdown(wait=True)
        runtime.engine.dispose()
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        root_runtime.engine.dispose()
