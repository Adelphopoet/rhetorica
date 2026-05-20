from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.infra.postgres.models import MetadataTableUsageOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select


class PostgresMetadataUsageStore:
    """Persist metadata table usage priors for ranking."""

    _DECAY_PER_DAY = 0.96

    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def usage_map(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
    ) -> dict[str, float]:
        workspace = self._scope_value(workspace_id)
        connector = self._scope_value(connector_name)
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            rows = session.execute(
                select(MetadataTableUsageOrm).where(
                    MetadataTableUsageOrm.workspace_id == workspace,
                    MetadataTableUsageOrm.connector_name == connector,
                )
            ).scalars()
            usage: dict[str, float] = {}
            for row in rows:
                decayed_score = self._decayed_score(row=row, now=now)
                if decayed_score != 0:
                    usage[row.table_fqn] = decayed_score
            return usage

    def record_successful_usage(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None:
        self._record_delta(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=table_fqns,
            positive_delta=1.0,
            negative_delta=0.0,
            occurred_at=occurred_at,
        )

    def record_negative_feedback(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None:
        self._record_delta(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=table_fqns,
            positive_delta=0.0,
            negative_delta=1.5,
            occurred_at=occurred_at,
        )

    def _record_delta(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        positive_delta: float,
        negative_delta: float,
        occurred_at: datetime | None,
    ) -> None:
        workspace = self._scope_value(workspace_id)
        connector = self._scope_value(connector_name)
        now = occurred_at or datetime.now(tz=UTC)
        normalized_fqns = self._normalize_table_fqns(table_fqns)
        if not normalized_fqns:
            return

        with self._db.session() as session:
            for table_fqn in normalized_fqns:
                row = session.execute(
                    select(MetadataTableUsageOrm).where(
                        MetadataTableUsageOrm.workspace_id == workspace,
                        MetadataTableUsageOrm.connector_name == connector,
                        MetadataTableUsageOrm.table_fqn == table_fqn,
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = MetadataTableUsageOrm(
                        id=str(uuid4()),
                        workspace_id=workspace,
                        connector_name=connector,
                        table_fqn=table_fqn,
                        positive_score=0.0,
                        negative_score=0.0,
                        last_used_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                row.positive_score = (
                    self._decay(
                        value=row.positive_score,
                        last_seen_at=row.last_used_at,
                        now=now,
                    )
                    + positive_delta
                )
                row.negative_score = (
                    self._decay(
                        value=row.negative_score,
                        last_seen_at=row.last_used_at,
                        now=now,
                    )
                    + negative_delta
                )
                row.last_used_at = now
                row.updated_at = now
            session.commit()

    @classmethod
    def _decayed_score(cls, *, row: MetadataTableUsageOrm, now: datetime) -> float:
        return cls._decay(
            value=row.positive_score - row.negative_score,
            last_seen_at=row.last_used_at,
            now=now,
        )

    @classmethod
    def _decay(
        cls,
        *,
        value: float,
        last_seen_at: datetime,
        now: datetime,
    ) -> float:
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        elapsed_days = max(0.0, (now - last_seen_at).total_seconds() / 86_400)
        return value * (cls._DECAY_PER_DAY**elapsed_days)

    @staticmethod
    def _scope_value(value: str | None) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else "default"

    @staticmethod
    def _normalize_table_fqns(table_fqns: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for table_fqn in table_fqns:
            if not isinstance(table_fqn, str):
                continue
            value = table_fqn.strip()
            key = value.lower()
            if not value or key in seen:
                continue
            seen.add(key)
            normalized.append(value)
        return normalized
