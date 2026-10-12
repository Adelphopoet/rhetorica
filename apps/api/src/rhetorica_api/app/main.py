from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.infra.langfuse import install_langfuse
from rhetorica_backend.infra.logging import configure_logging, get_logger
from rhetorica_backend.infra.postgres.migrations import run_migrations

from rhetorica_api.app.auth import (
    install_auth,
    seed_local_admin_account,
    validate_auth_token_peppers,
)
from rhetorica_api.app.mattermost_runtime import MattermostListenerSupervisor
from rhetorica_api.app.mcp import get_mcp_server
from rhetorica_api.app.oauth import validate_oauth_public_urls
from rhetorica_api.app.rate_limits import close_api_rate_limit_runtime, install_api_rate_limits
from rhetorica_api.app.request_logging import install_request_logging
from rhetorica_api.app.routes.agents import admin_router as admin_agents_router
from rhetorica_api.app.routes.agents import router as agents_router
from rhetorica_api.app.routes.airflow_connectors import router as airflow_connectors_router
from rhetorica_api.app.routes.analysis_exports import router as analysis_exports_router
from rhetorica_api.app.routes.auth import router as auth_router
from rhetorica_api.app.routes.chat import router as chat_router
from rhetorica_api.app.routes.chat_stream import router as chat_stream_router
from rhetorica_api.app.routes.connectors import router as connectors_router
from rhetorica_api.app.routes.documentation_connectors import (
    router as documentation_connectors_router,
)
from rhetorica_api.app.routes.execution import router as execution_router
from rhetorica_api.app.routes.files import router as files_router
from rhetorica_api.app.routes.gitlab_connectors import router as gitlab_connectors_router
from rhetorica_api.app.routes.kubernetes_connectors import router as kubernetes_connectors_router
from rhetorica_api.app.routes.llm_capabilities import router as llm_capabilities_router
from rhetorica_api.app.routes.llm_connectors import router as llm_connectors_router
from rhetorica_api.app.routes.mattermost_connectors import (
    router as mattermost_connectors_router,
)
from rhetorica_api.app.routes.mcp_connectors import router as mcp_connectors_router
from rhetorica_api.app.routes.oauth import router as oauth_router
from rhetorica_api.app.routes.rag_connectors import router as rag_connectors_router
from rhetorica_api.app.routes.rag_insights_review import router as rag_insights_review_router
from rhetorica_api.app.routes.scenario_workflows import router as scenario_workflows_router
from rhetorica_api.app.routes.scenarios import router as scenarios_router
from rhetorica_api.app.routes.sql_guard import router as sql_guard_router
from rhetorica_api.app.routes.users import router as users_router
from rhetorica_api.app.routes.victoriametrics_connectors import (
    router as victoriametrics_connectors_router,
)
from rhetorica_api.app.routes.workspace_skills import router as workspace_skills_router
from rhetorica_api.app.settings import settings
from rhetorica_api.app.telemetry import install_api_telemetry

configure_logging(
    service_name="api",
    environment=settings.app_env,
    log_level=settings.log_level,
    json_logs=settings.log_json,
)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "api.startup.begin",
        mattermost_listener_enabled=settings.mattermost_listener_enabled,
        run_migrations_on_startup=settings.run_migrations_on_startup,
    )
    if settings.run_migrations_on_startup:
        run_migrations()
        logger.info("api.startup.migrations_completed")
    validate_auth_token_peppers()
    if settings.mcp_enabled:
        validate_oauth_public_urls()
    seed_local_admin_account()
    logger.info("api.startup.auth_seed_completed")
    mattermost_supervisor = None
    if settings.mattermost_listener_enabled:
        mattermost_supervisor = MattermostListenerSupervisor()
        mattermost_supervisor.start()
        logger.info("api.startup.mattermost_supervisor_started")
    mcp_server = getattr(_app.state, "mcp_server", None)
    try:
        if mcp_server is not None:
            async with mcp_server.mcp.session_manager.run():
                yield
        else:
            yield
    finally:
        if mattermost_supervisor is not None:
            await mattermost_supervisor.stop()
        telemetry_runtime = getattr(_app.state, "telemetry_runtime", None)
        if telemetry_runtime is not None:
            telemetry_runtime.shutdown()
        await close_api_rate_limit_runtime()
        langfuse_runtime = getattr(_app.state, "langfuse_runtime", None)
        if langfuse_runtime is not None:
            langfuse_runtime.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(_request: Request, exc: WorkflowError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc), "code": exc.code}
        )

    app.include_router(scenario_workflows_router)
    install_api_rate_limits(app)
    install_auth(app)
    install_request_logging(app)

    @app.get("/healthz", tags=["health"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(admin_agents_router)
    app.include_router(users_router)
    app.include_router(sql_guard_router)
    app.include_router(execution_router)
    app.include_router(files_router)
    app.include_router(connectors_router)
    app.include_router(llm_connectors_router)
    app.include_router(llm_capabilities_router)
    app.include_router(mattermost_connectors_router)
    app.include_router(mcp_connectors_router)
    app.include_router(documentation_connectors_router)
    app.include_router(gitlab_connectors_router)
    app.include_router(airflow_connectors_router)
    app.include_router(kubernetes_connectors_router)
    app.include_router(victoriametrics_connectors_router)
    app.include_router(rag_connectors_router)
    app.include_router(workspace_skills_router)
    app.include_router(rag_insights_review_router)
    app.include_router(scenarios_router)
    app.include_router(analysis_exports_router)
    app.include_router(chat_router)
    app.include_router(chat_stream_router)

    if settings.mcp_enabled:
        app.include_router(oauth_router)
        mcp_server = get_mcp_server()
        mcp_http_app = mcp_server.mcp.streamable_http_app()
        app.router.routes.extend(mcp_http_app.routes)
        app.state.mcp_server = mcp_server

    app.state.telemetry_runtime = install_api_telemetry(
        app,
        enabled=settings.otel_enabled,
        service_version=settings.app_version,
        environment=settings.app_env,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        otlp_insecure=settings.otel_exporter_otlp_insecure,
    )
    app.state.langfuse_runtime = install_langfuse(
        enabled=settings.langfuse_enabled,
        service_version=settings.app_version,
        environment=settings.app_env,
    )

    return app


app = create_app()
