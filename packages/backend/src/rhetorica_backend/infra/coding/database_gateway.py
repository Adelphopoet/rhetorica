"""Engine-neutral guarded query gateway for governed coding tasks."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from rhetorica_backend.contracts.coding import (
    CodingDatabaseQueryRequest,
    QueryCapabilityClaims,
)
from rhetorica_backend.contracts.execution import ExecuteSqlRequest
from rhetorica_backend.domain.execution.policy import sql_guard_policy_for_connection
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.infra.coding.trino_gateway import (
    LiveDatabaseCapabilityResolver,
    QueryGatewayDenied,
)
from rhetorica_backend.sql_guard.models import SqlGuardPolicy


class QueryConcurrencyRegistry:
    """Bound synchronous database work per task-scoped capability."""

    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._lock = threading.RLock()

    @contextmanager
    def reserve(self, claims: QueryCapabilityClaims) -> Iterator[None]:
        scope_key = self._scope_key(claims)
        with self._lock:
            active = self._active.get(scope_key, 0)
            if active >= claims.max_concurrency:
                raise QueryGatewayDenied("query_concurrency_exceeded", status_code=429)
            self._active[scope_key] = active + 1
        try:
            yield
        finally:
            with self._lock:
                active = self._active.get(scope_key, 0)
                if active <= 1:
                    self._active.pop(scope_key, None)
                else:
                    self._active[scope_key] = active - 1

    @staticmethod
    def _scope_key(claims: QueryCapabilityClaims) -> str:
        principal = claims.principal
        return ":".join((
            principal.workspace_id,
            principal.run_id,
            principal.task_id or "",
            claims.connector_id,
        ))


class DatabaseQueryGateway:
    """Resolve any supported connector and execute it through the existing SQL guard."""

    def __init__(
        self,
        *,
        resolver: LiveDatabaseCapabilityResolver,
        execution: ExecutionService,
        concurrency: QueryConcurrencyRegistry | None = None,
    ) -> None:
        self._resolver = resolver
        self._execution = execution
        self._concurrency = concurrency or QueryConcurrencyRegistry()

    def execute(
        self, *, claims: QueryCapabilityClaims, request: CodingDatabaseQueryRequest
    ) -> dict[str, object]:
        connection = self._resolver.resolve_live(claims)
        remaining_ms = int((claims.expires_at - datetime.now(tz=UTC)).total_seconds() * 1000)
        if remaining_ms <= 0:
            raise QueryGatewayDenied("query_capability_expired", status_code=401)
        requested_policy = SqlGuardPolicy(
            row_limit=request.row_limit,
            timeout_ms=min(request.timeout_ms, remaining_ms),
        )
        connector_policy = sql_guard_policy_for_connection(connection, requested_policy)
        policy = connector_policy.model_copy(
            update={"timeout_ms": min(connector_policy.timeout_ms, requested_policy.timeout_ms)}
        )
        with self._concurrency.reserve(claims):
            result = self._execution.execute(
                ExecuteSqlRequest(
                    sql=request.sql,
                    connection=connection,
                    policy=policy,
                    workspace_id=claims.principal.workspace_id,
                    actor_id=claims.principal.actor_user_id,
                )
            )
        payload = result.model_dump(mode="json")
        size = len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))
        if size > claims.max_result_bytes:
            raise QueryGatewayDenied("query_result_budget_exceeded", status_code=413)
        return payload
