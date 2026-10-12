from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from rhetorica_backend.contracts.coding import (
    ExecutionPrincipal,
    IssueDbtProfileRequest,
    IssueQueryCapabilityRequest,
    QueryCapabilityClaims,
)
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.infra.coding.trino_gateway import (
    DatabaseQueryCapabilityIssuer,
    DbtGatewayProfileIssuer,
    QueryCapabilitySigner,
    QueryGatewayDenied,
    QueryGatewayProtocolError,
    QueryHandleRegistry,
    TrinoProtocolGateway,
    connector_endpoint_digest,
)


class ResolverStub:
    def __init__(self, connection: EngineConnectionConfig):
        self.connection = connection

    def resolve_for_issue(self, _request):
        return "trino-chd-data-governance", self.connection

    def resolve_live(self, _claims):
        return self.connection


def _principal(task_id: str = "task-1") -> ExecutionPrincipal:
    return ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="coding-e-test",
        entry_source="web_chat",
        root_agent_id="agent-1",
        executing_agent_id="agent-1",
        run_id="run-1",
        task_id=task_id,
    )


def _connection() -> EngineConnectionConfig:
    return EngineConnectionConfig(
        engine="trino",
        host="trino.internal",
        port=8443,
        username="svc_dbt",
        password="upstream-secret",
        catalog="iceberg",
        schema="analytics",
        secure=True,
        extra_settings={
            "join_distribution_type": "AUTOMATIC",
            "http_headers": {"Host": "trino-router.internal"},
        },
        extra_credentials={"tenant": "tenant-secret"},
    )


def _claims(task_id: str = "task-1", *, max_result_bytes: int = 1024 * 1024):
    connection = _connection()
    now = datetime.now(tz=UTC)
    return QueryCapabilityClaims(
        principal=_principal(task_id),
        connector_id="connector-1",
        connector_alias="trino-chd-data-governance",
        engine="trino",
        endpoint_digest=connector_endpoint_digest(connection),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        max_result_bytes=max_result_bytes,
        max_concurrency=1,
    )


def test_issued_dbt_profile_contains_only_gateway_capability() -> None:
    connection = _connection()
    signer = QueryCapabilitySigner("s" * 64)
    issuer = DbtGatewayProfileIssuer(
        resolver=ResolverStub(connection),
        signer=signer,
        gateway_base_url="http://coding-query-gateway:8092",
    )
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=5)

    issued = issuer.issue(
        IssueDbtProfileRequest(
            principal=_principal(),
            connector_id="connector-1",
            expires_at=expires_at,
        )
    )
    profile = json.loads(issued.runtime_profile.profiles_yml)
    output = profile["rhetorica"]["outputs"]["gateway"]
    token = output["http_headers"]["X-Rhetorica-Query-Capability"]

    assert output["host"] == "coding-query-gateway"
    assert output["prepared_statements_enabled"] is False
    assert "upstream-secret" not in issued.runtime_profile.profiles_yml
    assert "trino.internal" not in issued.runtime_profile.profiles_yml
    assert signer.verify(token).principal.task_id == "task-1"


def test_engine_neutral_capability_can_be_issued_for_clickhouse() -> None:
    connection = EngineConnectionConfig(
        engine="clickhouse",
        host="clickhouse.internal",
        port=8443,
        username="reader",
        password="secret",
        secure=True,
    )
    signer = QueryCapabilitySigner("s" * 64)
    issuer = DatabaseQueryCapabilityIssuer(
        resolver=ResolverStub(connection),
        signer=signer,
        query_endpoint="http://coding-query-gateway:8092/v1/query",
    )
    issued = issuer.issue(
        IssueQueryCapabilityRequest(
            principal=_principal(),
            connector_id="connector-1",
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )

    assert issued.engine == "clickhouse"
    assert signer.verify(issued.capability).engine == "clickhouse"
    assert "secret" not in issued.model_dump_json()


def test_gateway_rewrites_handles_and_never_forwards_client_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "upstream-query",
                    "infoUri": "https://trino.internal:8443/ui/query.html?upstream-query",
                    "nextUri": "https://trino.internal:8443/v1/statement/executing/1",
                    "partialCancelUri": "https://trino.internal:8443/v1/statement/executing/1/partialCancel",
                    "data": [[1]],
                    "stats": {},
                },
            )
        return httpx.Response(200, json={"id": "upstream-query", "data": [[2]], "stats": {}})

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    first = gateway.statement(
        claims=_claims(),
        sql="SELECT order_id FROM iceberg.analytics.orders",
        client_headers={
            "X-Trino-User": "attacker",
            "X-Trino-Catalog": "other",
            "X-Trino-Session": "",
            "X-Trino-Prepared-Statement": "",
            "X-Trino-Transaction-Id": "NONE",
        },
    )
    handle = first["id"]
    second = gateway.poll(claims=_claims(), handle=handle)

    assert first["nextUri"] == f"http://coding-query-gateway:8092/v1/query/{handle}"
    assert first["infoUri"] == f"http://coding-query-gateway:8092/v1/query/{handle}"
    assert second["data"] == [[2]]
    assert requests[0].headers["X-Trino-User"] == "svc_dbt"
    assert requests[0].headers["X-Trino-Catalog"] == "iceberg"
    assert requests[0].headers["Host"] == "trino-router.internal"
    assert "x-trino-prepared-statement" not in requests[0].headers


def test_gateway_rejects_upstream_uri_for_another_origin() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "query",
                "nextUri": "https://evil.example/v1/statement/executing/1",
                "stats": {},
            },
        )

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    with pytest.raises(QueryGatewayProtocolError, match="trino_upstream_uri_forbidden"):
        gateway.statement(claims=_claims(), sql="SELECT 1", client_headers={})


def test_gateway_rejects_foreign_task_handle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "query",
                "nextUri": "https://trino.internal:8443/v1/statement/executing/1",
                "stats": {},
            },
            request=request,
        )

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    first = gateway.statement(claims=_claims(), sql="SELECT 1", client_headers={})

    with pytest.raises(QueryGatewayDenied, match="query_handle_not_found"):
        gateway.poll(claims=_claims("task-2"), handle=first["id"])


def test_gateway_enforces_concurrency_and_releases_completed_handle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "query",
                "nextUri": "https://trino.internal:8443/v1/statement/executing/1",
                "stats": {},
            },
            request=request,
        )

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    claims = _claims()
    first = gateway.statement(claims=claims, sql="SELECT 1", client_headers={})

    with pytest.raises(QueryGatewayDenied, match="query_concurrency_exceeded"):
        gateway.statement(claims=claims, sql="SELECT 2", client_headers={})

    gateway.cancel(claims=claims, handle=first["id"])
    assert gateway.statement(claims=claims, sql="SELECT 3", client_headers={})["id"]


def test_gateway_cancels_when_result_budget_is_exceeded() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        return httpx.Response(
            200,
            json={
                "id": "query",
                "nextUri": "https://trino.internal:8443/v1/statement/executing/1",
                "data": [["too-large"]],
                "stats": {},
            },
            request=request,
        )

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    with pytest.raises(QueryGatewayDenied, match="query_result_budget_exceeded"):
        gateway.statement(claims=_claims(max_result_bytes=4), sql="SELECT 1", client_headers={})

    assert methods == ["POST", "DELETE"]


def test_gateway_rejects_redirect_without_following_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://evil.example/v1/statement"},
            request=request,
        )

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    with pytest.raises(QueryGatewayProtocolError, match="trino_redirect_forbidden"):
        gateway.statement(claims=_claims(), sql="SELECT 1", client_headers={})

    assert len(requests) == 1


def test_gateway_accepts_omitted_default_port_in_trino_next_uri() -> None:
    TrinoProtocolGateway._require_upstream_uri(
        "https://trino.internal/v1/statement/queued/query/token/1",
        "https://trino.internal:443",
    )


def test_upstream_headers_respect_connector_timeout_capability() -> None:
    connection = _connection().model_copy(
        update={"extra_settings": {"set_query_max_run_time": False}}
    )

    headers = TrinoProtocolGateway._upstream_headers(connection, _claims())

    assert "query_max_run_time" not in headers["X-Trino-Session"]


def test_gateway_rejects_impersonation_headers_before_upstream() -> None:
    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
    )

    with pytest.raises(QueryGatewayDenied, match="x-trino-role"):
        gateway.statement(
            claims=_claims(),
            sql="SELECT 1",
            client_headers={"X-Trino-Role": "system=admin"},
        )


def test_gateway_forwards_only_result_format_client_capabilities() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "upstream-query", "data": [], "stats": {}})

    gateway = TrinoProtocolGateway(
        resolver=ResolverStub(_connection()),
        registry=QueryHandleRegistry(),
        public_base_url="http://coding-query-gateway:8092",
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
    )

    # dbt-trino asks for parametric datetime so timestamp(p) columns carry precision.
    gateway.statement(
        claims=_claims(),
        sql="SELECT current_timestamp(3) AS loaded_at",
        client_headers={
            "X-Trino-Client-Capabilities": "PARAMETRIC_DATETIME, SESSION_AUTHORIZATION"
        },
    )
    gateway.statement(claims=_claims("task-2"), sql="SELECT 1", client_headers={})

    assert requests[0].headers["X-Trino-Client-Capabilities"] == "PARAMETRIC_DATETIME"
    assert "x-trino-client-capabilities" not in requests[1].headers
