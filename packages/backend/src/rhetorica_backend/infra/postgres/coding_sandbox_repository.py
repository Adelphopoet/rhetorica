from __future__ import annotations

import hashlib
import threading
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, cast

from rhetorica_backend.contracts.coding import (
    SandboxLeaseAcquisition,
    SandboxLeaseStatus,
    SandboxTaskLease,
)
from rhetorica_backend.infra.postgres.coding_sandbox_models import CodingSandboxLeaseOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import CursorResult


class CodingSandboxLeaseRepository:
    _sqlite_attempt_lock = threading.Lock()
    _active_statuses = (
        SandboxLeaseStatus.CREATED,
        SandboxLeaseStatus.STAGED,
        SandboxLeaseStatus.RUNNING,
        SandboxLeaseStatus.CHECKPOINTED,
        SandboxLeaseStatus.CANCEL_REQUESTED,
    )

    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    def acquire_attempt(self, lease: SandboxTaskLease) -> SandboxLeaseAcquisition:
        """Return the active attempt or atomically allocate the next terminal retry."""

        sqlite_lock = (
            self._sqlite_attempt_lock if self._db.engine.dialect.name == "sqlite" else nullcontext()
        )
        with sqlite_lock, self._db.session() as session:
            if self._db.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            elif self._db.engine.dialect.name == "postgresql":
                first_key, second_key = self._attempt_lock_keys(lease)
                session.execute(select(func.pg_advisory_xact_lock(first_key, second_key)))
            latest = session.scalar(
                select(CodingSandboxLeaseOrm)
                .where(
                    CodingSandboxLeaseOrm.workspace_id == lease.workspace_id,
                    CodingSandboxLeaseOrm.run_id == lease.run_id,
                    CodingSandboxLeaseOrm.task_id == lease.task_id,
                )
                .order_by(CodingSandboxLeaseOrm.attempt.desc())
                .limit(1)
            )
            if latest is not None and latest.status in self._active_statuses:
                active = self._contract(latest)
                self._require_compatible_retry(active=active, requested=lease)
                return SandboxLeaseAcquisition(lease=active, created=False)
            created = lease.model_copy(
                update={
                    "attempt": int(latest.attempt if latest is not None else 0) + 1,
                    "version": 1,
                }
            )
            session.add(CodingSandboxLeaseOrm(**created.model_dump(mode="python")))
            session.commit()
        return SandboxLeaseAcquisition(lease=created, created=True)

    def get_owned(
        self, *, lease_id: str, workspace_id: str, owner_user_id: str
    ) -> SandboxTaskLease | None:
        with self._db.session() as session:
            row = session.get(CodingSandboxLeaseOrm, lease_id)
            if (
                row is None
                or row.workspace_id != workspace_id
                or row.owner_user_id != owner_user_id
            ):
                return None
            return self._contract(row)

    def transition(
        self,
        *,
        lease_id: str,
        workspace_id: str,
        owner_user_id: str,
        expected_version: int,
        expected_statuses: tuple[str, ...],
        status: str,
        checkpoint_ref: str | None = None,
        candidate_artifact_id: str | None = None,
    ) -> SandboxTaskLease | None:
        values: dict[str, Any] = {
            "status": status,
            "version": CodingSandboxLeaseOrm.version + 1,
            "updated_at": datetime.now(tz=UTC),
        }
        if checkpoint_ref is not None:
            values["checkpoint_ref"] = checkpoint_ref
        if candidate_artifact_id is not None:
            values["candidate_artifact_id"] = candidate_artifact_id
        with self._db.session() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(CodingSandboxLeaseOrm)
                    .where(
                        CodingSandboxLeaseOrm.id == lease_id,
                        CodingSandboxLeaseOrm.workspace_id == workspace_id,
                        CodingSandboxLeaseOrm.owner_user_id == owner_user_id,
                        CodingSandboxLeaseOrm.version == expected_version,
                        CodingSandboxLeaseOrm.status.in_(expected_statuses),
                    )
                    .values(**values)
                ),
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            session.commit()
            row = session.get(CodingSandboxLeaseOrm, lease_id)
            if row is None:
                return None
            return self._contract(row)

    def list_reconcilable(self) -> list[SandboxTaskLease]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(CodingSandboxLeaseOrm).where(
                        CodingSandboxLeaseOrm.status.in_([
                            "created",
                            "staged",
                            "running",
                            "checkpointed",
                            "cancel_requested",
                        ])
                    )
                )
                .scalars()
                .all()
            )
        return [self._contract(row) for row in rows]

    @staticmethod
    def _contract(row: CodingSandboxLeaseOrm) -> SandboxTaskLease:
        return SandboxTaskLease.model_validate({
            column.name: getattr(row, column.name) for column in row.__table__.columns
        })

    @staticmethod
    def _attempt_lock_keys(lease: SandboxTaskLease) -> tuple[int, int]:
        scope = "\0".join((lease.workspace_id, lease.run_id, lease.task_id)).encode()
        digest = hashlib.sha256(scope).digest()
        return (
            int.from_bytes(digest[:4], byteorder="big", signed=True),
            int.from_bytes(digest[4:8], byteorder="big", signed=True),
        )

    @staticmethod
    def _require_compatible_retry(*, active: SandboxTaskLease, requested: SandboxTaskLease) -> None:
        fields = (
            "owner_user_id",
            "environment_profile_id",
            "environment_profile_revision",
            "image_digest",
            "runtime_class_name",
            "tool_versions",
            "software_manifest_digest",
            "phase",
        )
        if any(getattr(active, field) != getattr(requested, field) for field in fields):
            raise ValueError("active_sandbox_lease_conflict")
