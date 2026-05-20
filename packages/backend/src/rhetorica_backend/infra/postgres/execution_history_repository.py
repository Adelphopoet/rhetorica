from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, cast

from rhetorica_backend.contracts.execution import (
    EngineType,
    ExecutionHistoryItem,
    ExecutionStatus,
    QueryPreview,
)
from rhetorica_backend.infra.postgres.models import QueryExecutionHistoryOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.sql_guard.models import SqlGuardViolation
from sqlalchemy import select


class ExecutionHistoryRepository:
    """Persistence gateway for query execution history."""

    _TERMINAL_STATUSES: ClassVar[set[str]] = {"blocked", "succeeded", "failed", "cancelled"}

    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def create_queued(
        self,
        execution_id: str,
        workspace_id: str,
        engine: str,
        submitted_sql: str,
        timeout_ms: int,
        row_limit: int,
        connection_summary: dict[str, object],
        actor_id: str | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        row = QueryExecutionHistoryOrm(
            execution_id=execution_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            engine=engine,
            status="queued",
            submitted_sql=submitted_sql,
            normalized_sql=None,
            timeout_ms=timeout_ms,
            row_limit=row_limit,
            row_count=None,
            duration_ms=None,
            query_id=None,
            error_message=None,
            warnings=[],
            violations=[],
            preview_payload={"columns": [], "rows": []},
            connection_summary=connection_summary,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            cancel_requested_at=None,
        )

        with self._db.session() as session:
            session.add(row)
            session.commit()

    def create_running(
        self,
        execution_id: str,
        workspace_id: str,
        engine: str,
        submitted_sql: str,
        normalized_sql: str | None,
        timeout_ms: int,
        row_limit: int,
        warnings: list[str],
        violations: list[dict[str, str]],
        connection_summary: dict[str, object],
        status: str = "running",
        actor_id: str | None = None,
    ) -> None:
        now = datetime.now(tz=UTC)
        row = QueryExecutionHistoryOrm(
            execution_id=execution_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            engine=engine,
            status=status,
            submitted_sql=submitted_sql,
            normalized_sql=normalized_sql,
            timeout_ms=timeout_ms,
            row_limit=row_limit,
            row_count=None,
            duration_ms=None,
            query_id=None,
            error_message=None,
            warnings=warnings,
            violations=violations,
            preview_payload={"columns": [], "rows": []},
            connection_summary=connection_summary,
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now if status in self._TERMINAL_STATUSES else None,
            cancel_requested_at=None,
        )

        with self._db.session() as session:
            session.add(row)
            session.commit()

    def mark_running(
        self,
        execution_id: str,
        *,
        normalized_sql: str | None,
        warnings: list[str],
        violations: list[dict[str, str]],
    ) -> bool:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return False
            now = datetime.now(tz=UTC)
            row.status = "running"
            row.normalized_sql = normalized_sql
            row.warnings = warnings
            row.violations = violations
            row.updated_at = now
            row.started_at = now
            session.commit()
            return True

    def mark_blocked(
        self,
        execution_id: str,
        *,
        normalized_sql: str | None,
        warnings: list[str],
        violations: list[dict[str, str]],
        error_message: str,
    ) -> bool:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return False
            now = datetime.now(tz=UTC)
            row.status = "blocked"
            row.normalized_sql = normalized_sql
            row.warnings = warnings
            row.violations = violations
            row.error_message = error_message
            row.updated_at = now
            row.finished_at = now
            session.commit()
            return True

    def set_query_id(self, execution_id: str, query_id: str | None) -> bool:
        if query_id is None:
            return False
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return False
            row.query_id = query_id
            row.updated_at = datetime.now(tz=UTC)
            session.commit()
            return True

    def mark_cancel_requested(self, execution_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return False
            if row.status in self._TERMINAL_STATUSES:
                return False
            now = datetime.now(tz=UTC)
            row.status = "cancel_requested"
            row.updated_at = now
            row.cancel_requested_at = row.cancel_requested_at or now
            session.commit()
            return True

    def is_cancel_requested(self, execution_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return False
            return row.cancel_requested_at is not None or row.status == "cancel_requested"

    def complete(
        self,
        execution_id: str,
        status: str,
        query_id: str | None,
        row_count: int | None,
        duration_ms: int | None,
        error_message: str | None,
        preview_payload: dict[str, object] | None = None,
    ) -> None:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return
            now = datetime.now(tz=UTC)
            row.status = status
            row.updated_at = now
            row.finished_at = now
            row.query_id = query_id
            row.row_count = row_count
            row.duration_ms = duration_ms
            row.error_message = error_message
            if preview_payload is not None:
                row.preview_payload = preview_payload
            session.commit()

    def list_recent(
        self,
        workspace_id: str,
        limit: int,
        actor_id: str | None = None,
    ) -> list[ExecutionHistoryItem]:
        with self._db.session() as session:
            stmt = (
                select(QueryExecutionHistoryOrm)
                .where(QueryExecutionHistoryOrm.workspace_id == workspace_id)
            )
            if actor_id is not None:
                stmt = stmt.where(QueryExecutionHistoryOrm.actor_id == actor_id)
            stmt = stmt.order_by(QueryExecutionHistoryOrm.created_at.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()

        return [self._to_contract(row) for row in rows]

    def get(self, execution_id: str) -> ExecutionHistoryItem | None:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
        if row is None:
            return None
        return self._to_contract(row)

    @staticmethod
    def _to_contract(row: QueryExecutionHistoryOrm) -> ExecutionHistoryItem:
        violations = [SqlGuardViolation.model_validate(item) for item in row.violations]
        return ExecutionHistoryItem(
            execution_id=row.execution_id,
            workspace_id=row.workspace_id,
            actor_id=row.actor_id,
            engine=cast(EngineType, row.engine),
            status=cast(ExecutionStatus, row.status),
            submitted_sql=row.submitted_sql,
            normalized_sql=row.normalized_sql,
            timeout_ms=row.timeout_ms,
            row_limit=row.row_limit,
            row_count=row.row_count,
            duration_ms=row.duration_ms,
            query_id=row.query_id,
            error_message=row.error_message,
            warnings=row.warnings,
            violations=violations,
            preview=QueryPreview.model_validate(row.preview_payload or {}),
            connection_summary=row.connection_summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            cancel_requested_at=row.cancel_requested_at,
        )
