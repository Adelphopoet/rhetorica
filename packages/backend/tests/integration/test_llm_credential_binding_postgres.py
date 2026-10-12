"""PostgreSQL-only concurrency coverage for conversation credential binding."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.chat_models import (
    ChatConversationOrm,
    ChatLlmCredentialBindingOrm,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.secrets import InMemorySecretStore
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker


class _BlockingSecretStore(InMemorySecretStore):
    """Pause one credential update after the secret value has changed."""

    def __init__(self) -> None:
        super().__init__()
        self.block_writes = False
        self.observe_reads = False
        self.secret_written = Event()
        self.secret_read = Event()
        self.allow_commit = Event()

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        value = super().get_secret(workspace_id, secret_key)
        if self.observe_reads:
            self.secret_read.set()
        return value

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        super().set_secret(workspace_id, secret_key, value)
        if self.block_writes:
            self.secret_written.set()
            assert self.allow_commit.wait(timeout=5)


class _ResolutionBlockingSecretStore(InMemorySecretStore):
    """Block reads after recording how many resolutions reached SecretStore."""

    def __init__(self) -> None:
        super().__init__()
        self.block_reads = False
        self.first_read = Event()
        self.second_read = Event()
        self.allow_reads = Event()
        self._read_count = 0
        self._count_lock = Lock()

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        value = super().get_secret(workspace_id, secret_key)
        if not self.block_reads:
            return value
        with self._count_lock:
            self._read_count += 1
            read_count = self._read_count
        if read_count == 1:
            self.first_read.set()
        elif read_count == 2:
            self.second_read.set()
        assert self.allow_reads.wait(timeout=5)
        return value


class _CompensationBlockingSecretStore(InMemorySecretStore):
    """Pause compensation before it restores the prior secret value."""

    def __init__(self, *, prior_value: str) -> None:
        super().__init__()
        self.prior_value = prior_value
        self.block_restore = False
        self.observe_reads = False
        self.restore_started = Event()
        self.secret_read = Event()
        self.allow_restore = Event()

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        value = super().get_secret(workspace_id, secret_key)
        if self.observe_reads:
            self.secret_read.set()
        return value

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        if self.block_restore and value == self.prior_value:
            self.restore_started.set()
            assert self.allow_restore.wait(timeout=5)
        super().set_secret(workspace_id, secret_key, value)


class _FailingCommitSession:
    def __init__(self, session: Session, fail_next_commit: list[bool]) -> None:
        self._session = session
        self._fail_next_commit = fail_next_commit

    def __enter__(self):
        self._session.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._session.__exit__(*args)

    def __getattr__(self, name: str):
        return getattr(self._session, name)

    def commit(self) -> None:
        if self._fail_next_commit[0]:
            self._fail_next_commit[0] = False
            raise RuntimeError("simulated commit failure")
        self._session.commit()


def test_postgres_concurrent_first_calls_share_one_binding() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL concurrency coverage")

    schema = f"credential_binding_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        Base.metadata.create_all(engine)
        chat_repository = ChatRepository(runtime)
        conversation = chat_repository.create_conversation(workspace_id="ws-1", title="test")
        connector_repository = LlmConnectorRepository(
            db=runtime,
            secret_store=InMemorySecretStore(),
        )
        connector = connector_repository.upsert(
            UpsertLlmConnectorRequest(
                name="llm-main",
                workspace_id="ws-1",
                connection=LlmConnectionConfig(
                    base_url="https://llm.example.test/v1",
                    model="test-model",
                    api_keys=["key-a", "key-b"],
                ),
            )
        )
        repository = LlmCredentialBindingRepository(runtime)
        barrier = Barrier(2)

        def resolve() -> int:
            barrier.wait(timeout=5)
            return repository.resolve(
                conversation_id=conversation.id,
                llm_connector_id=connector.id,
                credential_set_version=1,
                credential_count=2,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _unused: resolve(), range(2)))

        assert len(set(results)) == 1
        with runtime.session() as session:
            rows = list(
                session.scalars(
                    select(ChatLlmCredentialBindingOrm).where(
                        ChatLlmCredentialBindingOrm.conversation_id == conversation.id,
                        ChatLlmCredentialBindingOrm.llm_connector_id == connector.id,
                    )
                )
            )
        assert len(rows) == 1
        assert rows[0].credential_index == results[0]

        # Both foreign keys are operational, not merely declared in metadata.
        with runtime.session() as session:
            conversation_row = session.get(ChatConversationOrm, conversation.id)
            assert conversation_row is not None
            session.delete(conversation_row)
            session.commit()
        with runtime.session() as session:
            assert session.get(
                ChatLlmCredentialBindingOrm,
                {
                    "conversation_id": conversation.id,
                    "llm_connector_id": connector.id,
                },
            ) is None

        replacement_conversation = chat_repository.create_conversation(
            workspace_id="ws-1",
            title="connector cascade",
        )
        repository.resolve(
            conversation_id=replacement_conversation.id,
            llm_connector_id=connector.id,
            credential_set_version=1,
            credential_count=2,
        )
        assert connector_repository.delete(connector.id, "ws-1") is True
        with runtime.session() as session:
            assert session.get(
                ChatLlmCredentialBindingOrm,
                {
                    "conversation_id": replacement_conversation.id,
                    "llm_connector_id": connector.id,
                },
            ) is None
    finally:
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_postgres_resolution_waits_for_connector_update_secret_snapshot() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL concurrency coverage")

    schema = f"credential_resolution_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        Base.metadata.create_all(engine)
        secret_store = _BlockingSecretStore()
        repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
        connector = repository.upsert(
            UpsertLlmConnectorRequest(
                name="llm-main",
                workspace_id="ws-1",
                connection=LlmConnectionConfig(
                    base_url="https://llm.example.test/v1",
                    model="test-model",
                    api_keys=["key-a", "key-b"],
                ),
            )
        )
        secret_store.block_writes = True

        update_request = UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.test/v1",
                model="test-model",
                api_keys=["key-c", "key-d"],
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            update = pool.submit(
                repository.update,
                connector.id,
                "ws-1",
                update_request,
            )
            assert secret_store.secret_written.wait(timeout=5)
            secret_store.observe_reads = True

            resolution = pool.submit(
                repository.get_connection_resolution,
                "ws-1",
                "llm-main",
            )
            # Without the connector row lock, resolution reads key-c/key-d now
            # while PostgreSQL still exposes version 1.
            assert not secret_store.secret_read.wait(timeout=0.5)

            secret_store.allow_commit.set()
            assert update.result(timeout=5) is not None
            resolved = resolution.result(timeout=5)

        assert resolved is not None
        assert resolved.credential_set_version == 2
        assert resolved.connection.api_keys == ["key-c", "key-d"]
    finally:
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_postgres_concurrent_resolutions_share_connector_read_lock() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL concurrency coverage")

    schema = f"credential_read_lock_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        Base.metadata.create_all(engine)
        secret_store = _ResolutionBlockingSecretStore()
        repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
        repository.upsert(
            UpsertLlmConnectorRequest(
                name="llm-main",
                workspace_id="ws-1",
                connection=LlmConnectionConfig(
                    base_url="https://llm.example.test/v1",
                    model="test-model",
                    api_keys=["key-a", "key-b"],
                ),
            )
        )
        secret_store.block_reads = True

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(repository.get_connection_resolution, "ws-1", "llm-main")
            assert secret_store.first_read.wait(timeout=5)
            second = pool.submit(repository.get_connection_resolution, "ws-1", "llm-main")
            # FOR SHARE allows both turns to resolve one connector in parallel.
            assert secret_store.second_read.wait(timeout=0.5)
            secret_store.allow_reads.set()
            first_resolution = first.result(timeout=5)
            second_resolution = second.result(timeout=5)

        assert first_resolution is not None
        assert second_resolution is not None
        assert first_resolution.credential_set_version == 1
        assert second_resolution.credential_set_version == 1
        assert first_resolution.connection.api_keys == ["key-a", "key-b"]
        assert second_resolution.connection.api_keys == ["key-a", "key-b"]
    finally:
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_postgres_resolution_waits_for_secret_compensation_before_rollback() -> None:
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL concurrency coverage")

    schema = f"credential_compensation_{uuid4().hex}"
    root_runtime = DatabaseRuntime(database_url)
    with root_runtime.engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))

    engine = root_runtime.engine.execution_options(schema_translate_map={None: schema})
    runtime = DatabaseRuntime(database_url)
    runtime.engine = engine
    runtime._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    reader_runtime = DatabaseRuntime(database_url)
    reader_runtime.engine = engine
    reader_runtime._session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    try:
        Base.metadata.create_all(engine)
        old_keys = ["key-a", "key-b"]
        secret_store = _CompensationBlockingSecretStore(
            prior_value=json.dumps(old_keys, separators=(",", ":")),
        )
        repository = LlmConnectorRepository(db=runtime, secret_store=secret_store)
        connector = repository.upsert(
            UpsertLlmConnectorRequest(
                name="llm-main",
                workspace_id="ws-1",
                connection=LlmConnectionConfig(
                    base_url="https://llm.example.test/v1",
                    model="test-model",
                    api_keys=old_keys,
                ),
            )
        )
        reader_repository = LlmConnectorRepository(
            db=reader_runtime,
            secret_store=secret_store,
        )

        fail_next_commit = [True]
        session_factory = runtime._session_factory

        def failing_session() -> _FailingCommitSession:
            return _FailingCommitSession(session_factory(), fail_next_commit)

        runtime.session = failing_session  # type: ignore[method-assign]
        secret_store.block_restore = True
        update_request = UpsertLlmConnectorRequest(
            name="llm-main",
            workspace_id="ws-1",
            connection=LlmConnectionConfig(
                base_url="https://llm.example.test/v1",
                model="test-model",
                api_keys=["key-c", "key-d"],
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            failed_update = pool.submit(
                repository.update,
                connector.id,
                "ws-1",
                update_request,
            )
            assert secret_store.restore_started.wait(timeout=5)
            secret_store.observe_reads = True

            resolution = pool.submit(
                reader_repository.get_connection_resolution,
                "ws-1",
                "llm-main",
            )
            # The connector row remains locked while compensation restores key-a/key-b.
            assert not secret_store.secret_read.wait(timeout=0.5)

            secret_store.allow_restore.set()
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                failed_update.result(timeout=5)
            resolved = resolution.result(timeout=5)

        assert resolved is not None
        assert resolved.credential_set_version == 1
        assert resolved.connection.api_keys == old_keys
    finally:
        with root_runtime.engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
