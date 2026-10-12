from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from rhetorica_backend.contracts.coding import (
    CodingDatabaseQueryRequest,
    IssueDbtProfileRequest,
    IssueQueryCapabilityRequest,
    QueryCapabilityClaims,
)
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.coding.database_gateway import DatabaseQueryGateway
from rhetorica_backend.infra.coding.trino_gateway import (
    DatabaseQueryCapabilityIssuer,
    DbtGatewayProfileIssuer,
    LiveDatabaseCapabilityResolver,
    QueryCapabilitySigner,
    QueryGatewayDenied,
    QueryGatewayProtocolError,
    QueryHandleRegistry,
    TrinoProtocolGateway,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from starlette.concurrency import run_in_threadpool


class GatewaySettings(BaseSettings):
    internal_token: str
    signing_secret: str
    database_url: str = Field(validation_alias="DATABASE_URL")
    public_base_url: str = "http://coding-query-gateway:8092"
    max_statement_bytes: int = 2 * 1024 * 1024
    max_capability_ttl_seconds: int = 7200

    model_config = SettingsConfigDict(extra="ignore", env_prefix="CODING_QUERY_GATEWAY_")


def _require_internal_token(request: Request, token: str | None) -> None:
    settings = cast(GatewaySettings, request.app.state.settings)
    if token != settings.internal_token:
        raise HTTPException(status_code=404, detail="Not found")


def _claims(request: Request, token: str | None) -> QueryCapabilityClaims:
    if not token:
        raise HTTPException(status_code=401, detail="query_capability_required")
    return cast(QueryCapabilitySigner, request.app.state.signer).verify(token)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = GatewaySettings()  # type: ignore[call-arg]
    db = DatabaseRuntime(settings.database_url)
    workspaces = WorkspaceRepository(db=db)
    resolver = LiveDatabaseCapabilityResolver(
        connectors=ConnectorRepository(db=db),
        workspaces=workspaces,
        agents=AgentProfileRepository(db=db),
    )
    signer = QueryCapabilitySigner(settings.signing_secret)
    app.state.settings = settings
    app.state.signer = signer
    app.state.issuer = DbtGatewayProfileIssuer(
        resolver=resolver,
        signer=signer,
        gateway_base_url=settings.public_base_url,
        max_ttl_seconds=settings.max_capability_ttl_seconds,
    )
    app.state.capability_issuer = DatabaseQueryCapabilityIssuer(
        resolver=resolver,
        signer=signer,
        query_endpoint=f"{settings.public_base_url.rstrip('/')}/v1/query",
        max_ttl_seconds=settings.max_capability_ttl_seconds,
    )
    execution, _execution_db = build_execution_service(settings.database_url)
    app.state.database_gateway = DatabaseQueryGateway(
        resolver=resolver,
        execution=execution,
    )
    app.state.gateway = TrinoProtocolGateway(
        resolver=resolver,
        registry=QueryHandleRegistry(),
        public_base_url=settings.public_base_url,
    )
    yield


app = FastAPI(
    title="rhetorica-coding-query-gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.exception_handler(QueryGatewayDenied)
async def denied_handler(_request: Request, exc: QueryGatewayDenied) -> JSONResponse:
    return JSONResponse({"detail": exc.reason}, status_code=exc.status_code)


@app.exception_handler(QueryGatewayProtocolError)
async def protocol_handler(_request: Request, exc: QueryGatewayProtocolError) -> JSONResponse:
    return JSONResponse({"detail": exc.reason}, status_code=exc.status_code)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/v1/dbt-profiles")
def issue_dbt_profile(
    payload: IssueDbtProfileRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, object]:
    _require_internal_token(request, token)
    issuer = cast(DbtGatewayProfileIssuer, request.app.state.issuer)
    return issuer.issue(payload).model_dump(mode="json")


@app.post("/internal/v1/query-capabilities")
def issue_query_capability(
    payload: IssueQueryCapabilityRequest,
    request: Request,
    token: Annotated[str | None, Header(alias="X-Rhetorica-Coding-Token")] = None,
) -> dict[str, object]:
    _require_internal_token(request, token)
    issuer = cast(DatabaseQueryCapabilityIssuer, request.app.state.capability_issuer)
    return issuer.issue(payload).model_dump(mode="json")


@app.post("/v1/query")
async def database_query(
    payload: CodingDatabaseQueryRequest,
    request: Request,
    capability: Annotated[
        str | None, Header(alias="X-Rhetorica-Query-Capability")
    ] = None,
) -> dict[str, object]:
    gateway = cast(DatabaseQueryGateway, request.app.state.database_gateway)
    return await run_in_threadpool(
        gateway.execute,
        claims=_claims(request, capability),
        request=payload,
    )


@app.post("/v1/statement")
async def statement(
    request: Request,
    capability: Annotated[
        str | None, Header(alias="X-Rhetorica-Query-Capability")
    ] = None,
) -> dict[str, object]:
    claims = _claims(request, capability)
    payload = await request.body()
    settings = cast(GatewaySettings, request.app.state.settings)
    if len(payload) > settings.max_statement_bytes:
        raise HTTPException(status_code=413, detail="sql_statement_too_large")
    try:
        sql = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="sql_statement_not_utf8") from exc
    gateway = cast(TrinoProtocolGateway, request.app.state.gateway)
    return await run_in_threadpool(
        gateway.statement,
        claims=claims,
        sql=sql,
        client_headers=request.headers,
    )


@app.get("/v1/query/{handle}")
def poll(
    handle: str,
    request: Request,
    capability: Annotated[
        str | None, Header(alias="X-Rhetorica-Query-Capability")
    ] = None,
) -> dict[str, object]:
    gateway = cast(TrinoProtocolGateway, request.app.state.gateway)
    return gateway.poll(claims=_claims(request, capability), handle=handle)


@app.delete("/v1/query/{handle}", status_code=204)
def cancel(
    handle: str,
    request: Request,
    capability: Annotated[
        str | None, Header(alias="X-Rhetorica-Query-Capability")
    ] = None,
) -> Response:
    gateway = cast(TrinoProtocolGateway, request.app.state.gateway)
    gateway.cancel(claims=_claims(request, capability), handle=handle)
    return Response(status_code=204)
