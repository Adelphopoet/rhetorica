from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rhetorica_backend.contracts.coding import (
    CodingDatabaseQueryRequest,
    ExecutionPrincipal,
    QueryCapabilityClaims,
)
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.infra.coding.database_gateway import DatabaseQueryGateway
from rhetorica_backend.infra.coding.trino_gateway import (
    QueryGatewayDenied,
    connector_endpoint_digest,
)


class ResolverStub:
    def __init__(self, connection: EngineConnectionConfig):
        self.connection = connection

    def resolve_live(self, _claims: QueryCapabilityClaims) -> EngineConnectionConfig:
        return self.connection


class ExecutionStub:
    def __init__(self, response: ExecuteSqlResponse):
        self.response = response
        self.requests: list[Any] = []

    def execute(self, request: Any) -> ExecuteSqlResponse:
        self.requests.append(request)
        return self.response


def _claims(connection: EngineConnectionConfig, *, max_result_bytes: int = 10_000):
    now = datetime.now(tz=UTC)
    return QueryCapabilityClaims(
        principal=ExecutionPrincipal(
            actor_user_id="user-1",
            workspace_id="workspace-1",
            entry_source="web_chat",
            executing_agent_id="agent-1",
            run_id="run-1",
            task_id="task-1",
        ),
        connector_id="connector-1",
        connector_alias="analytics",
        engine=connection.engine,
        endpoint_digest=connector_endpoint_digest(connection),
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
        max_result_bytes=max_result_bytes,
        max_concurrency=1,
    )


def test_generic_gateway_uses_any_existing_engine_and_server_owned_policy() -> None:
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="clickhouse.internal",
        port=8443,
        username="reader",
        secure=True,
        extra_settings={"query_timeout_ms": 2_000},
    )
    execution = ExecutionStub(
        ExecuteSqlResponse(
            execution_id="execution-1",
            status="succeeded",
            engine="clickhouse",
            preview=QueryPreview(columns=["value"], rows=[[1]]),
        )
    )
    gateway = DatabaseQueryGateway(
        resolver=ResolverStub(connection),  # type: ignore[arg-type]
        execution=execution,  # type: ignore[arg-type]
    )

    result = gateway.execute(
        claims=_claims(connection),
        request=CodingDatabaseQueryRequest(
            sql="SELECT 1 AS value", row_limit=17, timeout_ms=5_000
        ),
    )

    assert result["status"] == "succeeded"
    assert execution.requests[0].connection is connection
    assert execution.requests[0].workspace_id == "workspace-1"
    assert execution.requests[0].policy.row_limit == 17
    assert execution.requests[0].policy.timeout_ms == 2_000


def test_generic_gateway_fails_when_serialized_result_exceeds_capability_budget() -> None:
    connection = EngineConnectionConfig(
        engine="greenplum",
        host="greenplum.internal",
        port=5432,
        username="reader",
    )
    execution = ExecutionStub(
        ExecuteSqlResponse(
            execution_id="execution-1",
            status="succeeded",
            engine="greenplum",
            preview=QueryPreview(columns=["value"], rows=[["large-result"]]),
        )
    )
    gateway = DatabaseQueryGateway(
        resolver=ResolverStub(connection),  # type: ignore[arg-type]
        execution=execution,  # type: ignore[arg-type]
    )

    with pytest.raises(QueryGatewayDenied, match="query_result_budget_exceeded"):
        gateway.execute(
            claims=_claims(connection, max_result_bytes=1),
            request=CodingDatabaseQueryRequest(sql="SELECT 'large-result'"),
        )
