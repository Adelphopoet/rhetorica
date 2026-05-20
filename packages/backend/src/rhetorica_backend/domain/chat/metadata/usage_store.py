"""Usage history storage contracts for metadata ranking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class MetadataUsageStore(Protocol):
    """Storage contract for table usage priors."""

    def usage_map(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
    ) -> dict[str, float]: ...

    def record_successful_usage(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None: ...

    def record_negative_feedback(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None: ...


class InMemoryMetadataUsageStore:
    """Process-local fallback for tests and non-persistent runtimes."""

    _DECAY_PER_DAY = 0.96

    def __init__(self) -> None:
        self._usage_history: dict[tuple[str, str], dict[str, tuple[float, datetime]]] = {}

    def usage_map(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
    ) -> dict[str, float]:
        workspace = workspace_id or "default"
        connector = connector_name or "default"
        now = datetime.now(tz=UTC)
        usage = self._usage_history.get((workspace, connector), {})
        decayed_usage: dict[str, float] = {}
        for table_fqn, (score, last_seen_at) in usage.items():
            decayed_score = self._decay(value=score, last_seen_at=last_seen_at, now=now)
            if decayed_score != 0:
                decayed_usage[table_fqn] = decayed_score
        return decayed_usage

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
            delta=1.0,
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
            delta=-1.5,
            occurred_at=occurred_at,
        )

    def _record_delta(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        delta: float,
        occurred_at: datetime | None,
    ) -> None:
        workspace = workspace_id or "default"
        connector = connector_name or "default"
        now = occurred_at or datetime.now(tz=UTC)
        usage = self._usage_history.setdefault((workspace, connector), {})
        for table_fqn in self._normalize_table_fqns(table_fqns):
            current_score, last_seen_at = usage.get(table_fqn, (0.0, now))
            usage[table_fqn] = (
                self._decay(value=current_score, last_seen_at=last_seen_at, now=now) + delta,
                now,
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
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        elapsed_days = max(0.0, (now - last_seen_at).total_seconds() / 86_400)
        return value * (cls._DECAY_PER_DAY**elapsed_days)

    @staticmethod
    def _normalize_table_fqns(table_fqns: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for table_fqn in table_fqns:
            if not isinstance(table_fqn, str):
                continue
            value = table_fqn.strip()
            if not value or value.lower() in seen:
                continue
            seen.add(value.lower())
            normalized.append(value)
        return normalized
