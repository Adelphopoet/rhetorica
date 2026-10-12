"""PostgreSQL row-lock coverage for durable coding runs.

This test runs only against the explicitly configured disposable
``TEST_POSTGRES_URL`` database. It never uses a developer or production URL.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    AgentTask,
    CodingRun,
    CodingRunAdmission,
    CodingRunPhase,
    CodingRunStatus,
    EnvironmentProfile,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    RepositoryBinding,
)
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.migrations import get_alembic_config
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _runtime() -> tuple[DatabaseRuntime, DatabaseRuntime, str]:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for durable coding PostgreSQL coverage")
    schema = f"coding_run_{uuid4().hex}"
    root = DatabaseRuntime(database_url)
    with root.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = root.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(engine)
    return root, runtime, schema


def _cleanup(root: DatabaseRuntime, runtime: DatabaseRuntime, schema: str) -> None:
    runtime.engine.dispose()
    with root.engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    root.engine.dispose()


def _admission(*, owner_user_id: str, run_id: str) -> CodingRunAdmission:
    now = datetime.now(tz=UTC)
    run = CodingRun(
        id=run_id,
        workspace_id="default",
        owner_user_id=owner_user_id,
        source="web_scenario",
        scenario_id="scenario-1",
        root_agent_id="manager-1",
        developer_agent_id="developer-1",
        qa_agent_id="qa-1",
        idempotency_key="browser-retry-1",
        input_digest="a" * 64,
        phase=CodingRunPhase.PREPARING,
        status=CodingRunStatus.QUEUED,
        repository=RepositoryBinding(
            gitlab_connector_id="gitlab-1",
            project_id="group/project",
            target_branch="main",
            base_sha="b" * 40,
        ),
        environment_profile_id="coding-default",
        environment_profile_revision=1,
        policy_revision=1,
        created_at=now,
        deadline=now + timedelta(minutes=20),
    )

    scope = GitLabWriteScope(
        connector_id="gitlab-1",
        project_id="group/project",
        target_branches=["main"],
        allowed_paths=["models"],
    )
    return CodingRunAdmission(
        run=run,
        principal=ExecutionPrincipal(
            actor_user_id=owner_user_id,
            workspace_id="default",
            entry_source="web_scenario",
            root_agent_id="manager-1",
            executing_agent_id="developer-1",
            run_id=run.id,
        ),
        policy_snapshot=AgentPolicySnapshot(
            workspace_id="default",
            root_agent_id="manager-1",
            executing_agent_id="developer-1",
            root_settings=GovernedAgentSettings(
                revision=1,
                max_iterations=100,
                max_runtime_seconds=1200,
            ),
            executing_settings=GovernedAgentSettings(
                revision=1,
                max_iterations=3,
                max_runtime_seconds=300,
            ),
            resource_connector_id="gitlab-1",
            run_scope=scope,
        ),
        environment_profile=EnvironmentProfile(
            id="coding-default",
            revision=1,
            image_digest="registry.example/coding@sha256:" + "c" * 64,
            runtime_class_name="gvisor",
            max_runtime_seconds=1200,
            cpu_limit="1",
            memory_limit="1Gi",
            disk_limit_bytes=1024 * 1024 * 1024,
            pid_limit=256,
            created_at=now,
        ),
    )


def test_confirmation_lock_allows_admission_foreign_key_without_deadlock() -> None:
    from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
    from rhetorica_backend.infra.postgres.coding_plan_repository import CodingPlanRepository

    root, runtime, schema = _runtime()
    try:
        owner = UserAccountRepository(runtime).ensure_bootstrap_user("default")
        WorkspaceRepository(runtime).ensure_home_membership(user_id=owner.id)
        conversation = ChatRepository(runtime).create_conversation(
            "default", owner_user_id=owner.id
        )
        admission = _admission(owner_user_id=owner.id, run_id=str(uuid4()))
        admission = admission.model_copy(
            update={
                "run": admission.run.model_copy(
                    update={
                        "source": "web_chat",
                        "conversation_id": conversation.id,
                        "scenario_id": None,
                    }
                ),
                "principal": admission.principal.model_copy(update={"entry_source": "web_chat"}),
            }
        )

        def bound_wait(connection, *_):
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '1s'")

        event.listen(runtime.engine, "checkout", bound_wait)
        try:
            with CodingPlanRepository(runtime).locked(
                conversation_id=conversation.id, user=owner
            ) as state:
                result = CodingRunRepository(runtime).admit(admission)
                state["coding_chat"] = {"confirmed_run_id": result.run.id}
                assert result.created
        finally:
            event.remove(runtime.engine, "checkout", bound_wait)
        assert (
            CodingRunRepository(runtime)
            .get_admission(
                run_id=result.run.id,
                workspace_id="default",
                owner_user_id=owner.id,
            )
            .run.conversation_id
            == conversation.id
        )
    finally:
        _cleanup(root, runtime, schema)


def test_postgres_admission_and_task_events_are_atomic_under_race() -> None:
    root, runtime, schema = _runtime()
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        users = UserAccountRepository(runtime)
        owner = users.ensure_bootstrap_user("default")
        WorkspaceRepository(runtime).ensure_home_membership(user_id=owner.id)
        start = Barrier(5)

        def admit() -> object:
            start.wait()
            return CodingRunRepository(runtime).admit(
                _admission(owner_user_id=owner.id, run_id=str(uuid4()))
            )

        futures = [pool.submit(admit) for _ in range(4)]
        start.wait()
        admissions = [future.result(timeout=10) for future in futures]
        run_ids = {result.run.id for result in admissions}
        assert len(run_ids) == 1
        assert sum(result.created for result in admissions) == 1
        run_id = run_ids.pop()
        repository = CodingRunRepository(runtime)
        first = repository.create_task(
            AgentTask(
                id="developer-task",
                workspace_id="default",
                owner_user_id=owner.id,
                run_id=run_id,
                agent_profile_id="developer-1",
                role="developer",
                status="queued",
                max_iterations=3,
                max_runtime_seconds=300,
                instruction_artifact_id="artifact://developer",
            )
        )
        second = repository.create_task(
            first.model_copy(update={"id": "qa-task", "agent_profile_id": "qa-1", "role": "qa"})
        )
        update_start = Barrier(3)

        def checkpoint(task: AgentTask) -> AgentTask:
            update_start.wait()
            return CodingRunRepository(runtime).checkpoint_task(
                task.model_copy(update={"status": "running", "iterations_used": 1}),
                expected_version=task.version,
            )

        update_futures = [pool.submit(checkpoint, task) for task in (first, second)]
        update_start.wait()
        assert [future.result(timeout=10).version for future in update_futures] == [2, 2]
        events = repository.events(
            run_id=run_id,
            workspace_id="default",
            owner_user_id=owner.id,
        )
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    finally:
        pool.shutdown(wait=True)
        _cleanup(root, runtime, schema)


def test_root_budget_migration_preserves_usage_and_accepts_next_reservation(
    monkeypatch,
) -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for coding budget migration coverage")
    schema = f"coding_budget_migration_{uuid4().hex}"
    root = DatabaseRuntime(database_url)
    runtime: DatabaseRuntime | None = None
    with root.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    # Alembic stores the URL in ConfigParser, where literal percent signs in
    # URL-encoded query arguments must be doubled. DatabaseRuntime receives the
    # normal URL below.
    monkeypatch.setenv("DATABASE_URL", scoped_url.replace("%", "%%"))
    try:
        config = get_alembic_config()
        command.upgrade(config, "e0f1a2b3c4d5")
        runtime = DatabaseRuntime(scoped_url)
        users = UserAccountRepository(runtime)
        owner = users.ensure_bootstrap_user("default")
        WorkspaceRepository(runtime).ensure_home_membership(user_id=owner.id)
        repository = CodingRunRepository(runtime)
        run = repository.admit(_admission(owner_user_id=owner.id, run_id=str(uuid4()))).run
        developer = repository.create_task(
            AgentTask(
                id="developer-task",
                workspace_id="default",
                owner_user_id=owner.id,
                run_id=run.id,
                agent_profile_id="developer-1",
                role="developer",
                status="running",
                iterations_used=1,
                max_iterations=3,
                max_runtime_seconds=300,
                instruction_artifact_id="artifact://developer",
            )
        )
        repository.create_task(
            AgentTask(
                id="qa-task",
                workspace_id="default",
                owner_user_id=owner.id,
                run_id=run.id,
                agent_profile_id="qa-1",
                role="qa",
                status="running",
                iterations_used=1,
                max_iterations=3,
                max_runtime_seconds=300,
                instruction_artifact_id="artifact://qa",
            )
        )
        now = datetime.now(tz=UTC)
        earliest_first_attempt = now - timedelta(minutes=2)
        earliest_deadline = now + timedelta(minutes=8)
        with runtime.engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO coding_budget_ledger (
                        id, workspace_id, owner_user_id, run_id, agent_profile_id,
                        max_iterations, reserved_iterations, first_attempt_at,
                        deadline, updated_at
                    ) VALUES
                        (
                            'developer-ledger', 'default', :owner_user_id, :run_id,
                            'developer-1', 3, 1, :developer_first_attempt,
                            :developer_deadline, :updated_at
                        ),
                        (
                            'qa-ledger', 'default', :owner_user_id, :run_id,
                            'qa-1', 3, 1, :qa_first_attempt,
                            :qa_deadline, :updated_at
                        )
                """),
                {
                    "owner_user_id": owner.id,
                    "run_id": run.id,
                    "developer_first_attempt": earliest_first_attempt,
                    "developer_deadline": earliest_deadline,
                    "qa_first_attempt": now - timedelta(minutes=1),
                    "qa_deadline": now + timedelta(minutes=9),
                    "updated_at": now,
                },
            )
            connection.execute(
                text("""
                    INSERT INTO coding_iteration_reservation (
                        id, workspace_id, owner_user_id, run_id, agent_profile_id,
                        invocation_id, iteration, reserved_at
                    ) VALUES
                        (
                            'developer-reservation', 'default', :owner_user_id,
                            :run_id, 'developer-1', :developer_invocation, 1, :reserved_at
                        ),
                        (
                            'qa-reservation', 'default', :owner_user_id,
                            :run_id, 'qa-1', :qa_invocation, 1, :reserved_at
                        )
                """),
                {
                    "owner_user_id": owner.id,
                    "run_id": run.id,
                    "developer_invocation": f"{run.id}:developer-task:developer:0",
                    "qa_invocation": f"{run.id}:qa-task:qa:0",
                    "reserved_at": now,
                },
            )

        command.upgrade(config, "e1f2a3b4c5d6")

        with runtime.engine.connect() as connection:
            migrated = (
                connection.execute(
                    text("""
                    SELECT budget_owner_agent_id, max_iterations,
                           reserved_iterations, first_attempt_at, deadline
                    FROM coding_budget_ledger
                    WHERE run_id = :run_id
                """),
                    {"run_id": run.id},
                )
                .mappings()
                .one()
            )
        assert migrated["budget_owner_agent_id"] == "manager-1"
        assert migrated["max_iterations"] == 100
        assert migrated["reserved_iterations"] == 2
        assert migrated["first_attempt_at"] == earliest_first_attempt
        assert migrated["deadline"] == earliest_deadline

        command.upgrade(config, "head")
        retry = repository.reserve_iteration(
            run_id=run.id,
            task_id=developer.id,
            executing_agent_profile_id=developer.agent_profile_id,
            invocation_id=f"{run.id}:developer-task:developer:0",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        next_reservation = repository.reserve_iteration(
            run_id=run.id,
            task_id=developer.id,
            executing_agent_profile_id=developer.agent_profile_id,
            invocation_id=f"{run.id}:developer-task:developer:1",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        assert retry.iteration == 1
        assert next_reservation.iteration == 3
    finally:
        if runtime is not None:
            runtime.engine.dispose()
        with root.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        root.engine.dispose()


def test_workspace_coding_defaults_concurrent_save_has_one_winner():
    from rhetorica_backend.contracts.coding import UpsertWorkspaceCodingDefaults
    from rhetorica_backend.domain.coding.errors import CodingRunError
    from rhetorica_backend.infra.postgres.coding_defaults_repository import CodingDefaultsRepository
    from rhetorica_backend.infra.postgres.workspace_models import WorkspacePolicyAuditEventOrm
    from sqlalchemy import select

    root, runtime, schema = _runtime()
    try:
        owner, _ = UserAccountRepository(runtime).ensure_local_admin(
            workspace_id="default", login="defaults-owner", password_hash="test-only"
        )
        WorkspaceRepository(runtime).ensure_home_membership(user_id=owner.id)
        repo = CodingDefaultsRepository(runtime)
        gate = Barrier(2)

        def save():
            gate.wait(timeout=10)
            try:
                return repo.replace(
                    workspace_id="default",
                    actor_user_id=owner.id,
                    request=UpsertWorkspaceCodingDefaults(expected_revision=0, resources=None),
                ).revision
            except CodingRunError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: save(), range(2)))
        assert outcomes.count(1) == 1
        assert outcomes.count("coding_defaults_revision_conflict") == 1
        assert repo.get("default").revision == 1
        with runtime.session() as session:
            events = list(
                session.scalars(
                    select(WorkspacePolicyAuditEventOrm).where(
                        WorkspacePolicyAuditEventOrm.target_type == "coding_defaults"
                    )
                )
            )
            assert len(events) == 1
            assert events[0].current_value["revision"] == 1
    finally:
        _cleanup(root, runtime, schema)


@pytest.mark.parametrize("precreated", [False, True])
def test_shared_sdk_session_migration_preserves_chat_history_and_replay(monkeypatch, precreated):
    import asyncio
    import json

    from rhetorica_backend.infra.postgres.agent_session import PostgresAgentSessionStore
    from rhetorica_backend.infra.postgres.chat_repository import ChatRepository

    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for session migration coverage")
    schema = f"sdk_sessions_{uuid4().hex}"
    root = DatabaseRuntime(database_url)
    with root.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE_URL", scoped_url.replace("%", "%%"))
    runtime = DatabaseRuntime(scoped_url)
    try:
        config = get_alembic_config()
        command.upgrade(config, "e2f3a4b5c6d7")
        conversation = ChatRepository(runtime).create_conversation(
            workspace_id="default", title="Migration fixture"
        )
        with runtime.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO chat_agent_session (conversation_id, next_sequence, context_metadata, created_at, updated_at) VALUES (:id, 1, :metadata, now(), now())"
                ),
                {"id": conversation.id, "metadata": json.dumps({"preserved": True})},
            )
            connection.execute(
                text(
                    "INSERT INTO chat_agent_session_item (id, conversation_id, sequence, item_type, item_data, created_at) VALUES (:item, :id, 0, 'user', :data, now())"
                ),
                {
                    "item": str(uuid4()),
                    "id": conversation.id,
                    "data": json.dumps({"role": "user", "content": "retain this history"}),
                },
            )
        if precreated:
            runtime.create_schema()
        command.upgrade(config, "8b32f90d6a41")
        from rhetorica_backend.infra.postgres.agent_session_models import (
            AgentSessionItemOrm,
            AgentSessionOrm,
        )
        from sqlalchemy import inspect

        inspector = inspect(runtime.engine)
        for table in (AgentSessionOrm.__table__, AgentSessionItemOrm.__table__):
            assert {item["name"] for item in inspector.get_unique_constraints(table.name)} == {
                constraint.name
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            assert {
                item["name"]
                for item in inspector.get_indexes(table.name)
                if not item.get("duplicates_constraint")
            } == {index.name for index in table.indexes}
        store = PostgresAgentSessionStore(
            runtime, conversation.owner_user_id, workspace_id="default"
        )
        session = store.for_conversation(conversation.id)
        assert asyncio.run(session.get_items()) == [
            {"role": "user", "content": "retain this history"}
        ]
        assert asyncio.run(session.get_context_metadata()) == {"preserved": True}
        store.begin_operation(conversation.id, "test-operation")
        asyncio.run(session.add_items([{"role": "assistant", "content": "partial"}]))
        store.begin_operation(conversation.id, "test-operation")
        assert len(asyncio.run(session.get_items())) == 1
        command.downgrade(config, "e2f3a4b5c6d7")
        command.upgrade(config, "8b32f90d6a41")
        assert len(asyncio.run(store.for_conversation(conversation.id).get_items())) == 1
    finally:
        _cleanup(root, runtime, schema)


def test_postgres_model_usage_replay_and_chat_increment_are_atomic():
    from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm
    from rhetorica_backend.infra.postgres.chat_repository import ChatRepository

    root, runtime, schema = _runtime()
    try:
        owner = UserAccountRepository(runtime).ensure_bootstrap_user("default")
        WorkspaceRepository(runtime).ensure_home_membership(user_id=owner.id)
        conversation = ChatRepository(runtime).create_conversation(
            "default", owner_user_id=owner.id
        )
        admission = _admission(owner_user_id=owner.id, run_id=str(uuid4()))
        admission = admission.model_copy(
            update={"run": admission.run.model_copy(update={"conversation_id": conversation.id})}
        )
        runs = CodingRunRepository(runtime)
        run = runs.admit(admission).run
        task = runs.create_task(
            AgentTask(
                id=str(uuid4()),
                workspace_id="default",
                owner_user_id=owner.id,
                run_id=run.id,
                agent_profile_id="developer-1",
                role="developer",
                status="running",
                max_iterations=3,
                max_runtime_seconds=300,
                instruction_artifact_id="artifact://developer",
            )
        )
        scope = {
            "run_id": run.id,
            "task_id": task.id,
            "workspace_id": "default",
            "owner_user_id": owner.id,
        }
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_input_tokens": 70,
            "cache_write_input_tokens": 0,
            "cache_usage_reported_calls": 1,
            "usage_reported": True,
        }
        for invocation in ("one", "two"):
            runs.reserve_iteration(
                **scope, invocation_id=invocation, executing_agent_profile_id=task.agent_profile_id
            )
        barrier = Barrier(5)

        def record(invocation):
            barrier.wait()
            CodingRunRepository(runtime).record_model_usage(
                **scope, invocation_id=invocation, usage=usage
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(record, invocation) for invocation in ("one", "one", "two", "two")
            ]
            barrier.wait()
            for future in futures:
                future.result(timeout=10)
        with runtime.session() as session:
            saved = session.get(ChatConversationOrm, conversation.id)
            assert (
                saved.llm_input_tokens,
                saved.llm_output_tokens,
                saved.llm_cached_input_tokens,
            ) == (200, 40, 140)
    finally:
        _cleanup(root, runtime, schema)
