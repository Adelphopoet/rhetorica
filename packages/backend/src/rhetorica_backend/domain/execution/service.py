from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Event, RLock, Thread
from time import perf_counter
from typing import ClassVar
from uuid import uuid4

from rhetorica_backend.contracts.execution import (
    CancelExecutionResponse,
    ExecuteSqlRequest,
    ExecuteSqlResponse,
    ExecutionDetailResponse,
    ExecutionHistoryItem,
    ExecutionStatus,
    QueryPreview,
    sql_dialect_for_engine,
)
from rhetorica_backend.contracts.sql_guard import SqlGuardRequest
from rhetorica_backend.domain.execution.models import (
    ActiveExecution,
    EngineAdapter,
    EngineRunRequest,
    EngineRunResult,
)
from rhetorica_backend.infra.postgres.execution_history_repository import ExecutionHistoryRepository
from rhetorica_backend.sql_guard.models import SqlGuardViolation
from rhetorica_backend.sql_guard.service import SqlGuardService


@dataclass
class _ActiveExecutionState:
    active_execution: ActiveExecution


@dataclass
class _FetchOutcome:
    result: EngineRunResult | None = None
    error: Exception | None = None


class ExecutionService:
    """Runs guarded SQL against configured engines and tracks execution history."""

    _TERMINAL_STATUSES: ClassVar[set[str]] = {"blocked", "succeeded", "failed", "cancelled"}
    _CANCELLED_MESSAGE: ClassVar[str] = "Execution cancelled by user."
    _BLOCKED_MESSAGE: ClassVar[str] = "SQL blocked by guard policy."
    _MISSING_RESULT_MESSAGE: ClassVar[str] = "Execution finished without result."

    def __init__(
        self,
        history_repository: ExecutionHistoryRepository,
        adapters: dict[str, EngineAdapter],
        sql_guard_service: SqlGuardService | None = None,
    ):
        self._logger = logging.getLogger(__name__)
        self._history_repository = history_repository
        self._adapters = adapters
        self._sql_guard = sql_guard_service or SqlGuardService()
        self._active_executions: dict[str, _ActiveExecutionState] = {}
        self._cancel_requested: set[str] = set()
        self._lock = RLock()

    def execute(self, request: ExecuteSqlRequest) -> ExecuteSqlResponse:
        actor_id = getattr(request, "actor_id", None)
        return self._execute(
            request=request,
            execution_id=str(uuid4()),
            precreated=False,
            actor_id=actor_id,
        )

    def execute_prepared(
        self,
        execution_id: str,
        request: ExecuteSqlRequest,
    ) -> ExecuteSqlResponse:
        actor_id = getattr(request, "actor_id", None)
        return self._execute(
            request=request,
            execution_id=execution_id,
            precreated=True,
            actor_id=actor_id,
        )

    def create_queued_execution(
        self,
        execution_id: str,
        request: ExecuteSqlRequest,
    ) -> ExecuteSqlResponse:
        actor_id = getattr(request, "actor_id", None)
        self._history_repository.create_queued(
            execution_id=execution_id,
            workspace_id=request.workspace_id,
            engine=request.connection.engine,
            submitted_sql=request.sql,
            timeout_ms=request.policy.timeout_ms,
            row_limit=request.policy.row_limit,
            connection_summary=request.connection.redacted_dict(),
            actor_id=actor_id,
        )
        return ExecuteSqlResponse(
            execution_id=execution_id,
            status="queued",
            engine=request.connection.engine,
        )

    def get(self, execution_id: str) -> ExecutionHistoryItem | None:
        return self._history_repository.get(execution_id)

    def mark_enqueue_failed(self, execution_id: str, error_message: str) -> None:
        self._history_repository.complete(
            execution_id=execution_id,
            status="failed",
            query_id=None,
            row_count=None,
            duration_ms=0,
            error_message=error_message,
        )

    def get_detail(
        self,
        execution_id: str,
        result: ExecuteSqlResponse | None = None,
    ) -> ExecutionDetailResponse | None:
        history = self.get(execution_id)
        if history is None:
            return None
        if result is None and (
            history.preview.columns
            or history.preview.rows
            or history.status in {"succeeded", "failed", "blocked", "cancelled"}
        ):
            result = ExecuteSqlResponse(
                execution_id=history.execution_id,
                status=history.status,
                engine=history.engine,
                query_id=history.query_id,
                normalized_sql=history.normalized_sql,
                row_count=history.row_count,
                duration_ms=history.duration_ms,
                warnings=history.warnings,
                violations=history.violations,
                preview=history.preview,
                error_message=history.error_message,
            )
        return ExecutionDetailResponse(history=history, result=result)

    def _execute(
        self,
        *,
        request: ExecuteSqlRequest,
        execution_id: str,
        precreated: bool,
        actor_id: str | None = None,
    ) -> ExecuteSqlResponse:
        if precreated and self._history_repository.is_cancel_requested(execution_id):
            self._history_repository.complete(
                execution_id=execution_id,
                status="cancelled",
                query_id=None,
                row_count=None,
                duration_ms=0,
                error_message=self._CANCELLED_MESSAGE,
            )
            return self._build_cancelled_response(
                execution_id=execution_id,
                request=request,
                normalized_sql=request.sql,
                applied_row_limit=request.policy.row_limit,
                duration_ms=0,
                query_id=None,
                warnings=[],
                violations=[],
            )

        dialect = request.dialect or sql_dialect_for_engine(request.connection.engine)
        guard_decision = self._sql_guard.guard(
            SqlGuardRequest(
                sql=request.sql,
                dialect=dialect,
                engine=request.connection.engine,
                policy=request.policy,
            )
        )

        warnings = list(guard_decision.warnings)
        violations = [violation.model_dump(mode="json") for violation in guard_decision.violations]

        if not guard_decision.allowed:
            if precreated:
                self._history_repository.mark_blocked(
                    execution_id=execution_id,
                    normalized_sql=guard_decision.normalized_sql,
                    warnings=warnings,
                    violations=violations,
                    error_message=self._BLOCKED_MESSAGE,
                )
            else:
                self._history_repository.create_running(
                    execution_id=execution_id,
                    workspace_id=request.workspace_id,
                    engine=request.connection.engine,
                    submitted_sql=request.sql,
                    normalized_sql=guard_decision.normalized_sql,
                    timeout_ms=request.policy.timeout_ms,
                    row_limit=request.policy.row_limit,
                    warnings=warnings,
                    violations=violations,
                    connection_summary=request.connection.redacted_dict(),
                    status="blocked",
                    actor_id=actor_id,
                )
            return ExecuteSqlResponse(
                execution_id=execution_id,
                status="blocked",
                engine=request.connection.engine,
                normalized_sql=guard_decision.normalized_sql,
                applied_row_limit=guard_decision.applied_row_limit,
                warnings=guard_decision.warnings,
                violations=guard_decision.violations,
                error_message=self._BLOCKED_MESSAGE,
            )

        normalized_sql = guard_decision.normalized_sql or request.sql

        if precreated:
            self._history_repository.mark_running(
                execution_id=execution_id,
                normalized_sql=normalized_sql,
                warnings=warnings,
                violations=violations,
            )
        else:
            self._history_repository.create_running(
                execution_id=execution_id,
                workspace_id=request.workspace_id,
                engine=request.connection.engine,
                submitted_sql=request.sql,
                normalized_sql=normalized_sql,
                timeout_ms=request.policy.timeout_ms,
                row_limit=request.policy.row_limit,
                warnings=warnings,
                violations=violations,
                connection_summary=request.connection.redacted_dict(),
                status="running",
                actor_id=actor_id,
            )

        adapter = self._adapters.get(request.connection.engine)
        if adapter is None:
            error = f"No execution adapter configured for engine '{request.connection.engine}'."
            self._history_repository.complete(
                execution_id=execution_id,
                status="failed",
                query_id=None,
                row_count=None,
                duration_ms=0,
                error_message=error,
            )
            return ExecuteSqlResponse(
                execution_id=execution_id,
                status="failed",
                engine=request.connection.engine,
                normalized_sql=normalized_sql,
                applied_row_limit=guard_decision.applied_row_limit,
                duration_ms=0,
                warnings=guard_decision.warnings,
                violations=guard_decision.violations,
                error_message=error,
            )

        active_execution: ActiveExecution | None = None
        started = perf_counter()

        try:
            active_execution = adapter.start(
                connection=request.connection,
                request=EngineRunRequest(
                    execution_id=execution_id,
                    sql=normalized_sql,
                    timeout_ms=request.policy.timeout_ms,
                    row_limit=request.policy.row_limit,
                ),
            )
            self._register_active(execution_id, active_execution)
            self._history_repository.set_query_id(execution_id, active_execution.query_id)

            result = self._fetch_with_cancel_monitor(
                execution_id=execution_id,
                active_execution=active_execution,
            )
            duration_ms = int((perf_counter() - started) * 1000)
            self._history_repository.set_query_id(execution_id, result.query_id)

            if self._is_cancel_requested(execution_id):
                self._history_repository.complete(
                    execution_id=execution_id,
                    status="cancelled",
                    query_id=result.query_id,
                    row_count=None,
                    duration_ms=duration_ms,
                    error_message=self._CANCELLED_MESSAGE,
                )
                return self._build_cancelled_response(
                    execution_id=execution_id,
                    request=request,
                    normalized_sql=normalized_sql,
                    applied_row_limit=guard_decision.applied_row_limit,
                    duration_ms=duration_ms,
                    query_id=result.query_id,
                    warnings=guard_decision.warnings,
                    violations=guard_decision.violations,
                )

            self._history_repository.complete(
                execution_id=execution_id,
                status="succeeded",
                query_id=result.query_id,
                row_count=result.row_count,
                duration_ms=duration_ms,
                error_message=None,
                preview_payload=QueryPreview(
                    columns=result.columns,
                    rows=result.rows,
                ).model_dump(mode="json"),
            )
            return ExecuteSqlResponse(
                execution_id=execution_id,
                status="succeeded",
                engine=request.connection.engine,
                query_id=result.query_id,
                normalized_sql=normalized_sql,
                applied_row_limit=guard_decision.applied_row_limit,
                row_count=result.row_count,
                duration_ms=duration_ms,
                warnings=guard_decision.warnings,
                violations=guard_decision.violations,
                preview=QueryPreview(columns=result.columns, rows=result.rows),
            )
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            cancelled = self._is_cancel_requested(execution_id)
            status: ExecutionStatus = "cancelled" if cancelled else "failed"
            message = self._CANCELLED_MESSAGE if cancelled else str(exc)
            self._history_repository.complete(
                execution_id=execution_id,
                status=status,
                query_id=getattr(active_execution, "query_id", None),
                row_count=None,
                duration_ms=duration_ms,
                error_message=message,
            )
            return ExecuteSqlResponse(
                execution_id=execution_id,
                status=status,
                engine=request.connection.engine,
                query_id=getattr(active_execution, "query_id", None),
                normalized_sql=normalized_sql,
                applied_row_limit=guard_decision.applied_row_limit,
                duration_ms=duration_ms,
                warnings=guard_decision.warnings,
                violations=guard_decision.violations,
                error_message=message,
            )
        finally:
            if active_execution is not None:
                try:
                    active_execution.close()
                finally:
                    self._unregister_active(execution_id)

    def cancel(self, execution_id: str) -> CancelExecutionResponse:
        history = self._history_repository.get(execution_id)
        if history is None:
            return CancelExecutionResponse(
                execution_id=execution_id,
                cancelled=False,
                status="failed",
                message="Execution id is unknown.",
            )
        if history.status in self._TERMINAL_STATUSES:
            return CancelExecutionResponse(
                execution_id=execution_id,
                cancelled=False,
                status=history.status,
                message="Execution is not active.",
            )

        self._history_repository.mark_cancel_requested(execution_id)
        with self._lock:
            self._cancel_requested.add(execution_id)
            state = self._active_executions.get(execution_id)

        if state is not None:
            try:
                state.active_execution.cancel()
            except Exception as exc:
                return CancelExecutionResponse(
                    execution_id=execution_id,
                    cancelled=False,
                    status="failed",
                    message=f"Cancel failed: {exc}",
                )

        return CancelExecutionResponse(
            execution_id=execution_id,
            cancelled=True,
            status="cancel_requested",
            message="Cancellation requested.",
        )

    def list_history(
        self,
        workspace_id: str,
        limit: int,
        actor_id: str | None = None,
    ) -> list[ExecutionHistoryItem]:
        return self._history_repository.list_recent(
            workspace_id=workspace_id,
            limit=limit,
            actor_id=actor_id,
        )

    def _register_active(
        self,
        execution_id: str,
        active_execution: ActiveExecution,
    ) -> None:
        with self._lock:
            self._active_executions[execution_id] = _ActiveExecutionState(
                active_execution=active_execution
            )

    def _unregister_active(self, execution_id: str) -> None:
        with self._lock:
            self._active_executions.pop(execution_id, None)
            self._cancel_requested.discard(execution_id)

    def _is_cancel_requested(self, execution_id: str) -> bool:
        with self._lock:
            local_requested = execution_id in self._cancel_requested
        return local_requested or self._history_repository.is_cancel_requested(execution_id)

    def _fetch_with_cancel_monitor(
        self,
        *,
        execution_id: str,
        active_execution: ActiveExecution,
    ) -> EngineRunResult:
        outcome = _FetchOutcome()
        completed = Event()

        def _target() -> None:
            try:
                outcome.result = active_execution.fetch()
            except Exception as exc:  # pragma: no cover - propagated below
                outcome.error = exc
            finally:
                completed.set()

        thread = Thread(target=_target, daemon=True)
        thread.start()

        cancel_sent = False
        while not completed.wait(0.1):
            if cancel_sent or not self._history_repository.is_cancel_requested(execution_id):
                continue
            cancel_sent = True
            with self._lock:
                self._cancel_requested.add(execution_id)
            try:
                active_execution.cancel()
            except Exception:
                self._logger.exception(
                    "Execution cancel callback failed", extra={"execution_id": execution_id}
                )

        thread.join()
        if outcome.error is not None:
            raise outcome.error
        if outcome.result is None:
            raise RuntimeError(self._MISSING_RESULT_MESSAGE)
        return outcome.result

    def _build_cancelled_response(
        self,
        *,
        execution_id: str,
        request: ExecuteSqlRequest,
        normalized_sql: str | None,
        applied_row_limit: int | None,
        duration_ms: int,
        query_id: str | None,
        warnings: list[str],
        violations: list[SqlGuardViolation],
    ) -> ExecuteSqlResponse:
        return ExecuteSqlResponse(
            execution_id=execution_id,
            status="cancelled",
            engine=request.connection.engine,
            query_id=query_id,
            normalized_sql=normalized_sql,
            applied_row_limit=applied_row_limit,
            duration_ms=duration_ms,
            warnings=warnings,
            violations=violations,
            error_message=self._CANCELLED_MESSAGE,
        )
