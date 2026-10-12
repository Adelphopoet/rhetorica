from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, cast

from rhetorica_backend.contracts.execution import (
    EngineType,
    ExecutionHistoryItem,
    ExecutionStatus,
    QueryPreview,
)
from rhetorica_backend.domain.execution.snapshots import SqlResultSnapshot
from rhetorica_backend.infra.postgres.chat_models import (
    ChatConversationOrm,
    ChatMessageOrm,
)
from rhetorica_backend.infra.postgres.models import QueryExecutionHistoryOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.sql_guard.models import SqlGuardViolation
from sqlalchemy import exists, or_, select


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
        snapshot: SqlResultSnapshot | None = None,
        warnings: list[str] | None = None,
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
            if warnings is not None:
                row.warnings = warnings
            if snapshot is not None:
                row.snapshot_storage_key = snapshot.storage_key
                row.snapshot_columns = list(snapshot.columns)
                row.snapshot_row_count = snapshot.row_count
                row.snapshot_size_bytes = snapshot.size_bytes
                row.snapshot_sha256 = snapshot.sha256
                row.snapshot_owner_user_id = snapshot.owner_user_id
                row.snapshot_conversation_id = snapshot.conversation_id
                row.snapshot_possibly_truncated = snapshot.possibly_truncated
                row.snapshot_created_at = snapshot.created_at
            session.commit()

    def list_recent(
        self,
        workspace_id: str,
        limit: int,
        actor_id: str | None = None,
    ) -> list[ExecutionHistoryItem]:
        with self._db.session() as session:
            stmt = select(QueryExecutionHistoryOrm).where(
                QueryExecutionHistoryOrm.workspace_id == workspace_id
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

    def get_snapshot(self, execution_id: str) -> SqlResultSnapshot | None:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
        return self._snapshot_from_row(row) if row is not None else None

    def list_snapshots_for_cleanup(
        self,
        *,
        orphan_before: datetime,
        limit: int = 100,
    ) -> list[SqlResultSnapshot]:
        """Return explicitly deleted or stale-unlinked snapshots for idempotent cleanup."""
        from rhetorica_backend.infra.postgres.scenario_workflow_models import ScenarioExecutionOrm

        conversation_exists = exists(
            select(ChatConversationOrm.id).where(
                ChatConversationOrm.id == QueryExecutionHistoryOrm.snapshot_conversation_id
            )
        )
        message_exists = exists(
            select(ChatMessageOrm.id).where(
                ChatMessageOrm.execution_id == QueryExecutionHistoryOrm.execution_id
            )
        )
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(QueryExecutionHistoryOrm)
                    .where(
                        QueryExecutionHistoryOrm.snapshot_storage_key.is_not(None),
                        ~QueryExecutionHistoryOrm.execution_id.in_(
                            select(ScenarioExecutionOrm.execution_id)
                        ),
                        or_(
                            QueryExecutionHistoryOrm.snapshot_delete_pending.is_(True),
                            ~conversation_exists,
                            (QueryExecutionHistoryOrm.snapshot_created_at < orphan_before)
                            & ~message_exists,
                        ),
                    )
                    .order_by(QueryExecutionHistoryOrm.snapshot_created_at.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        snapshots: list[SqlResultSnapshot] = []
        for row in rows:
            snapshot = self._snapshot_from_row(row)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def finish_snapshot_cleanup(self, execution_id: str) -> None:
        with self._db.session() as session:
            row = session.get(QueryExecutionHistoryOrm, execution_id)
            if row is None:
                return
            row.snapshot_storage_key = None
            row.snapshot_columns = None
            row.snapshot_row_count = None
            row.snapshot_size_bytes = None
            row.snapshot_sha256 = None
            row.snapshot_owner_user_id = None
            row.snapshot_conversation_id = None
            row.snapshot_possibly_truncated = None
            row.snapshot_created_at = None
            row.snapshot_delete_pending = False
            row.updated_at = datetime.now(tz=UTC)
            session.commit()

    @staticmethod
    def _snapshot_from_row(row: QueryExecutionHistoryOrm) -> SqlResultSnapshot | None:
        if (
            row.snapshot_storage_key is None
            or row.snapshot_columns is None
            or row.snapshot_row_count is None
            or row.snapshot_size_bytes is None
            or row.snapshot_sha256 is None
            or row.snapshot_owner_user_id is None
            or row.snapshot_conversation_id is None
            or row.snapshot_created_at is None
        ):
            return None
        return SqlResultSnapshot(
            execution_id=row.execution_id,
            workspace_id=row.workspace_id,
            owner_user_id=row.snapshot_owner_user_id,
            conversation_id=row.snapshot_conversation_id,
            storage_key=row.snapshot_storage_key,
            columns=tuple(row.snapshot_columns),
            row_count=row.snapshot_row_count,
            size_bytes=row.snapshot_size_bytes,
            sha256=row.snapshot_sha256,
            possibly_truncated=bool(row.snapshot_possibly_truncated),
            created_at=row.snapshot_created_at,
        )

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
