"""Versioned coding environment profile persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.contracts.coding import (
    EnvironmentProfile,
    UpsertEnvironmentProfileRequest,
)
from rhetorica_backend.infra.postgres.coding_sandbox_models import CodingEnvironmentProfileOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


class CodingEnvironmentConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("coding_environment_revision_conflict")


class CodingEnvironmentRepository:
    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def list_latest(self) -> list[EnvironmentProfile]:
        with self._db.session() as session:
            latest = (
                select(
                    CodingEnvironmentProfileOrm.id,
                    func.max(CodingEnvironmentProfileOrm.revision).label("revision"),
                )
                .group_by(CodingEnvironmentProfileOrm.id)
                .subquery()
            )
            rows = session.execute(
                select(CodingEnvironmentProfileOrm)
                .join(
                    latest,
                    (CodingEnvironmentProfileOrm.id == latest.c.id)
                    & (CodingEnvironmentProfileOrm.revision == latest.c.revision),
                )
                .order_by(CodingEnvironmentProfileOrm.id)
            ).scalars()
            return [self._contract(row) for row in rows]

    def get(self, *, profile_id: str, revision: int | None = None) -> EnvironmentProfile | None:
        with self._db.session() as session:
            statement = select(CodingEnvironmentProfileOrm).where(
                CodingEnvironmentProfileOrm.id == profile_id
            )
            if revision is None:
                statement = statement.order_by(CodingEnvironmentProfileOrm.revision.desc()).limit(1)
            else:
                statement = statement.where(CodingEnvironmentProfileOrm.revision == revision)
            row = session.execute(statement).scalar_one_or_none()
            return self._contract(row) if row is not None else None

    def replace(
        self,
        *,
        request: UpsertEnvironmentProfileRequest,
        actor_user_id: str,
        image_digest: str,
        runtime_class_name: str,
        tool_versions: dict[str, str],
        software_manifest_digest: str,
    ) -> EnvironmentProfile:
        with self._db.session() as session:
            current_revision = session.execute(
                select(func.max(CodingEnvironmentProfileOrm.revision)).where(
                    CodingEnvironmentProfileOrm.id == request.id
                )
            ).scalar_one()
            current = int(current_revision or 0)
            if current != request.expected_revision:
                raise CodingEnvironmentConflictError(current)
            created_at = datetime.now(tz=UTC)
            profile = EnvironmentProfile(
                id=request.id,
                revision=current + 1,
                image_digest=image_digest,
                runtime_class_name=runtime_class_name,
                tool_versions=tool_versions,
                max_runtime_seconds=request.max_runtime_seconds,
                cpu_limit=request.cpu_limit,
                memory_limit=request.memory_limit,
                disk_limit_bytes=request.disk_limit_bytes,
                pid_limit=request.pid_limit,
                output_limit_bytes=request.output_limit_bytes,
                software_manifest_digest=software_manifest_digest,
                dependency_sources=request.dependency_sources,
                pip_dependencies=request.pip_dependencies,
                created_at=created_at,
            )
            row = CodingEnvironmentProfileOrm(
                **profile.model_dump(mode="python"), created_by=actor_user_id
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                latest = session.execute(
                    select(func.max(CodingEnvironmentProfileOrm.revision)).where(
                        CodingEnvironmentProfileOrm.id == request.id
                    )
                ).scalar_one()
                raise CodingEnvironmentConflictError(int(latest or 0)) from exc
            return profile

    @staticmethod
    def _contract(row: CodingEnvironmentProfileOrm) -> EnvironmentProfile:
        return EnvironmentProfile(
            id=row.id,
            revision=row.revision,
            image_digest=row.image_digest,
            runtime_class_name=row.runtime_class_name,
            tool_versions=dict(row.tool_versions or {}),
            max_runtime_seconds=row.max_runtime_seconds,
            cpu_limit=row.cpu_limit,
            memory_limit=row.memory_limit,
            disk_limit_bytes=row.disk_limit_bytes,
            pid_limit=row.pid_limit,
            output_limit_bytes=row.output_limit_bytes,
            software_manifest_digest=row.software_manifest_digest,
            dependency_sources=list(row.dependency_sources or []),
            pip_dependencies=list(row.pip_dependencies or []),
            created_at=row.created_at,
        )
