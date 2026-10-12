from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread

import pytest
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlRequest,
    ExecuteSqlResponse,
)
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
    ResultBlockConsumer,
)
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.execution.snapshots import SqlResultSnapshot
from rhetorica_backend.infra.postgres.execution_history_repository import ExecutionHistoryRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


class _FakeActiveExecution(ActiveExecution):
    def __init__(self) -> None:
        self.query_id = "fake-query-id"
        self.cancelled = False

    def fetch(self) -> EngineRunResult:
        return EngineRunResult(
            query_id=self.query_id,
            columns=["value"],
            rows=[[1], [2]],
            row_count=2,
        )

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        return None


class _BlockingActiveExecution(_FakeActiveExecution):
    def __init__(self, started: Event, cancelled: Event) -> None:
        super().__init__()
        self._started = started
        self._cancelled = cancelled

    def fetch(self) -> EngineRunResult:
        self._started.set()
        self._cancelled.wait(timeout=5)
        raise RuntimeError

    def cancel(self) -> None:
        super().cancel()
        self._cancelled.set()


class _StreamingActiveExecution(_FakeActiveExecution):
    def fetch_streaming(
        self,
        *,
        consumer: ResultBlockConsumer | None,
        preview_row_limit: int,
    ) -> EngineRunResult:
        rows = [[1], [2], [3], [4]]
        if consumer is not None:
            consumer.start(["value"])
            consumer.write_rows(rows[:2])
            consumer.write_rows(rows[2:])
        return EngineRunResult(
            query_id=self.query_id,
            columns=["value"],
            rows=rows[:preview_row_limit],
            row_count=len(rows),
        )


class _SnapshotCaptureStore:
    def __init__(self) -> None:
        self.columns: list[str] = []
        self.rows: list[list[object]] = []
        self.snapshot: SqlResultSnapshot | None = None
        self.deleted: list[SqlResultSnapshot] = []
        self._identity: dict[str, str] = {}

    def begin(self, **identity: str) -> _SnapshotCaptureStore:
        self._identity = identity
        return self

    def start(self, columns: Iterable[str]) -> None:
        self.columns = list(columns)

    def write_rows(self, rows: Iterable[Iterable[object]]) -> None:
        self.rows.extend([list(row) for row in rows])

    def finish(self, *, possibly_truncated: bool) -> SqlResultSnapshot:
        self.snapshot = SqlResultSnapshot(
            execution_id=self._identity["execution_id"],
            workspace_id=self._identity["workspace_id"],
            owner_user_id=self._identity["owner_user_id"],
            conversation_id=self._identity["conversation_id"],
            storage_key="memory://snapshot",
            columns=tuple(self.columns),
            row_count=len(self.rows),
            size_bytes=1,
            sha256="a" * 64,
            possibly_truncated=possibly_truncated,
            created_at=datetime.now(tz=UTC),
        )
        return self.snapshot

    def abort(self) -> None:
        return None

    def iter_rows(self, snapshot: SqlResultSnapshot) -> Iterator[list[object]]:
        del snapshot
        yield from self.rows

    def delete(self, snapshot: SqlResultSnapshot) -> None:
        self.deleted.append(snapshot)


class _FakeAdapter(EngineAdapter):
    def __init__(self, active_execution: ActiveExecution | None = None) -> None:
        self._active_execution = active_execution or _FakeActiveExecution()

    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        _ = connection, request
        return self._active_execution


class _StartupTimeoutAdapter(EngineAdapter):
    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution:
        _ = connection, request
        raise TimeoutError("Impala startup timed out after 50 ms.")


def _service(
    tmp_path: Path,
    adapter: EngineAdapter | None = None,
    snapshot_store: _SnapshotCaptureStore | None = None,
    preview_row_limit: int = 500,
) -> ExecutionService:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/exec_history.db")
    db.create_schema()
    repo = ExecutionHistoryRepository(db)
    return ExecutionService(
        history_repository=repo,
        adapters={"clickhouse": adapter or _FakeAdapter()},
        snapshot_store=snapshot_store,
        preview_row_limit=preview_row_limit,
    )


def _request(sql: str) -> ExecuteSqlRequest:
    return ExecuteSqlRequest(
        sql=sql,
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="default",
            database="default",
        ),
        workspace_id="ws-test",
    )


def test_execute_blocked_sql_is_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)

    response = service.execute(_request("INSERT INTO t SELECT 1"))

    assert response.status == "blocked"
    history = service.list_history(workspace_id="ws-test", limit=10)
    assert len(history) == 1
    assert history[0].status == "blocked"


def test_execute_success_returns_preview_and_history(tmp_path: Path) -> None:
    service = _service(tmp_path)

    response = service.execute(_request("SELECT 1"))

    assert response.status == "succeeded"
    assert response.row_count == 2
    assert response.preview.rows == [[1], [2]]

    history = service.list_history(workspace_id="ws-test", limit=10)
    assert len(history) == 1
    assert history[0].status == "succeeded"
    assert history[0].row_count == 2
    assert history[0].preview.rows == [[1], [2]]


def test_execute_describe_is_allowed_and_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)

    response = service.execute(_request("DESCRIBE dm.ft_order"))

    assert response.status == "succeeded"
    assert response.normalized_sql == "DESCRIBE dm.ft_order"

    history = service.list_history(workspace_id="ws-test", limit=10)
    assert len(history) == 1
    assert history[0].status == "succeeded"
    assert history[0].normalized_sql == "DESCRIBE dm.ft_order"


def test_execute_persists_failed_status_after_impala_startup_timeout(tmp_path: Path) -> None:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/impala_timeout.db")
    db.create_schema()
    service = ExecutionService(
        history_repository=ExecutionHistoryRepository(db),
        adapters={"impala": _StartupTimeoutAdapter()},
    )
    request = ExecuteSqlRequest(
        sql="DESCRIBE base_dm_v_l2.v_strg_stock_fa_d",
        connection=EngineConnectionConfig(
            engine="impala",
            host="impala.local",
            port=21050,
            username="analyst",
        ),
        workspace_id="ws-impala-timeout",
    )

    response = service.execute(request)

    assert response.status == "failed"
    assert response.error_message == "Impala startup timed out after 50 ms."
    history = service.list_history(workspace_id="ws-impala-timeout", limit=10)
    assert len(history) == 1
    assert history[0].status == "failed"
    assert history[0].error_message == "Impala startup timed out after 50 ms."


def test_prepared_execution_transitions_from_queued_to_succeeded(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request("SELECT 1")

    queued = service.create_queued_execution("exec-queued", request)
    assert queued.status == "queued"
    assert service.get("exec-queued") is not None
    assert service.get("exec-queued").started_at is None

    response = service.execute_prepared("exec-queued", request)

    assert response.status == "succeeded"
    history = service.get("exec-queued")
    assert history is not None
    assert history.status == "succeeded"
    assert history.started_at is not None
    assert history.query_id == "fake-query-id"


def test_cancelled_before_worker_start_returns_cancelled(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request("SELECT 1")

    service.create_queued_execution("exec-cancel-before-start", request)
    cancel = service.cancel("exec-cancel-before-start")
    response = service.execute_prepared("exec-cancel-before-start", request)

    assert cancel.cancelled is True
    assert cancel.status == "cancel_requested"
    assert response.status == "cancelled"
    history = service.get("exec-cancel-before-start")
    assert history is not None
    assert history.status == "cancelled"


def test_cancel_running_execution_marks_it_cancelled(tmp_path: Path) -> None:
    started = Event()
    cancelled = Event()
    service = _service(
        tmp_path,
        adapter=_FakeAdapter(
            active_execution=_BlockingActiveExecution(started=started, cancelled=cancelled)
        ),
    )
    request = _request("SELECT 1")
    service.create_queued_execution("exec-running-cancel", request)

    result: dict[str, object] = {}

    def _run() -> None:
        result["response"] = service.execute_prepared("exec-running-cancel", request)

    worker = Thread(target=_run, daemon=True)
    worker.start()
    assert started.wait(timeout=5)

    cancel = service.cancel("exec-running-cancel")
    worker.join(timeout=5)

    assert cancel.cancelled is True
    response = result["response"]
    assert isinstance(response, ExecuteSqlResponse)
    assert response.status == "cancelled"
    history = service.get("exec-running-cancel")
    assert history is not None
    assert history.status == "cancelled"


def test_service_passes_actor_id_to_repository(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request("SELECT 1")
    request.actor_id = "user-123"

    # Test create_queued_execution with actor_id on request
    service.create_queued_execution("exec-with-actor", request)

    history = service.get("exec-with-actor")
    assert history is not None
    assert history.actor_id == "user-123"


def test_service_stores_actor_id_on_direct_execute(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request("SELECT 1")
    request.actor_id = "user-456"

    response = service.execute(request)

    assert response.status == "succeeded"
    history = service.list_history(workspace_id="ws-test", limit=10)
    assert len(history) == 1
    assert history[0].actor_id == "user-456"


def test_streaming_execution_persists_full_snapshot_but_bounds_preview(
    tmp_path: Path,
) -> None:
    snapshots = _SnapshotCaptureStore()
    service = _service(
        tmp_path,
        adapter=_FakeAdapter(_StreamingActiveExecution()),
        snapshot_store=snapshots,
        preview_row_limit=2,
    )
    request = _request("SELECT 1")
    request.actor_id = "user-1"
    request.conversation_id = "conversation-1"

    response = service.execute(request)

    assert response.status == "succeeded"
    assert response.row_count == 4
    assert response.preview.rows == [[1], [2]]
    assert snapshots.rows == [[1], [2], [3], [4]]
    persisted = service.get_snapshot(response.execution_id)
    assert persisted is not None
    assert persisted.owner_user_id == "user-1"
    assert persisted.conversation_id == "conversation-1"
    assert persisted.row_count == 4


@pytest.mark.parametrize("engine", ["clickhouse", "trino", "greenplum", "impala"])
def test_streaming_snapshot_contract_is_engine_neutral(
    tmp_path: Path,
    engine: str,
) -> None:
    snapshots = _SnapshotCaptureStore()
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/{engine}_exec_history.db")
    db.create_schema()
    service = ExecutionService(
        history_repository=ExecutionHistoryRepository(db),
        adapters={engine: _FakeAdapter(_StreamingActiveExecution())},
        snapshot_store=snapshots,
        preview_row_limit=2,
    )
    request = ExecuteSqlRequest(
        sql="SELECT 1",
        connection=EngineConnectionConfig(
            engine=engine,  # type: ignore[arg-type]
            host="localhost",
            port=8123,
            username="test",
            database="default",
        ),
        workspace_id="ws-test",
        actor_id="user-1",
        conversation_id="conversation-1",
    )

    response = service.execute(request)

    assert response.status == "succeeded"
    assert response.engine == engine
    assert response.preview.rows == [[1], [2]]
    assert snapshots.rows == [[1], [2], [3], [4]]
    persisted = service.get_snapshot(response.execution_id)
    assert persisted is not None
    assert persisted.row_count == 4


def test_snapshot_object_is_deleted_when_metadata_commit_fails(tmp_path: Path) -> None:
    snapshots = _SnapshotCaptureStore()
    service = _service(
        tmp_path,
        adapter=_FakeAdapter(_StreamingActiveExecution()),
        snapshot_store=snapshots,
        preview_row_limit=2,
    )
    original_complete = service._history_repository.complete

    def fail_snapshot_commit(**kwargs: object) -> None:
        if kwargs.get("snapshot") is not None:
            raise RuntimeError("metadata commit failed")
        original_complete(**kwargs)  # type: ignore[arg-type]

    service._history_repository.complete = fail_snapshot_commit  # type: ignore[method-assign]
    request = _request("SELECT 1")
    request.actor_id = "user-1"
    request.conversation_id = "conversation-1"

    response = service.execute(request)

    assert response.status == "failed"
    assert len(snapshots.deleted) == 1
    assert snapshots.deleted[0].execution_id == response.execution_id


def test_list_history_filtered_by_actor_id(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request("SELECT 1")

    # Create executions from different actors
    request.actor_id = "actor-A"
    service.execute(request)

    request.actor_id = "actor-B"
    service.execute(request)

    request.actor_id = "actor-A"
    service.execute(request)

    # List all
    all_history = service.list_history(workspace_id="ws-test", limit=100)
    assert len(all_history) == 3

    # Filter by actor-A
    actor_a_history = service.list_history(workspace_id="ws-test", limit=100, actor_id="actor-A")
    assert len(actor_a_history) == 2
    assert all(h.actor_id == "actor-A" for h in actor_a_history)

    # Filter by actor-B
    actor_b_history = service.list_history(workspace_id="ws-test", limit=100, actor_id="actor-B")
    assert len(actor_b_history) == 1
    assert actor_b_history[0].actor_id == "actor-B"
