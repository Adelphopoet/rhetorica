from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

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
)
from rhetorica_backend.domain.execution.service import ExecutionService
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


class _FakeAdapter(EngineAdapter):
    def __init__(self, active_execution: ActiveExecution | None = None) -> None:
        self._active_execution = active_execution or _FakeActiveExecution()

    def start(self, connection: EngineConnectionConfig, request: EngineRunRequest) -> ActiveExecution:
        _ = connection, request
        return self._active_execution


def _service(
    tmp_path: Path,
    adapter: EngineAdapter | None = None,
) -> ExecutionService:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/exec_history.db")
    db.create_schema()
    repo = ExecutionHistoryRepository(db)
    return ExecutionService(history_repository=repo, adapters={"clickhouse": adapter or _FakeAdapter()})


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
        adapter=_FakeAdapter(active_execution=_BlockingActiveExecution(started=started, cancelled=cancelled)),
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
