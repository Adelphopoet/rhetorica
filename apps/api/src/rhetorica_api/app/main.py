from contextlib import asynccontextmanager

from fastapi import FastAPI
from rhetorica_backend.infra.logging import configure_logging, get_logger
from rhetorica_backend.infra.postgres.migrations import run_migrations

from rhetorica_api.app.auth import install_auth, seed_local_admin_account
from rhetorica_api.app.mattermost_runtime import MattermostListenerSupervisor
from rhetorica_api.app.request_logging import install_request_logging
from rhetorica_api.app.routes.analysis_exports import router as analysis_exports_router
from rhetorica_api.app.routes.auth import router as auth_router
from rhetorica_api.app.routes.chat import router as chat_router
from rhetorica_api.app.routes.chat_stream import router as chat_stream_router
from rhetorica_api.app.routes.confluence_connectors import (
    router as confluence_connectors_router,
)
from rhetorica_api.app.routes.connectors import router as connectors_router
from rhetorica_api.app.routes.execution import router as execution_router
from rhetorica_api.app.routes.jira_connectors import router as jira_connectors_router
from rhetorica_api.app.routes.llm_capabilities import router as llm_capabilities_router
from rhetorica_api.app.routes.llm_connectors import router as llm_connectors_router
from rhetorica_api.app.routes.mattermost_connectors import (
    router as mattermost_connectors_router,
)
from rhetorica_api.app.routes.mcp import router as mcp_router
from rhetorica_api.app.routes.openmetadata_connectors import (
    router as openmetadata_connectors_router,
)
from rhetorica_api.app.routes.rag_connectors import router as rag_connectors_router
from rhetorica_api.app.routes.rag_insights_review import router as rag_insights_review_router
from rhetorica_api.app.routes.scenarios import router as scenarios_router
from rhetorica_api.app.routes.sql_guard import router as sql_guard_router
from rhetorica_api.app.routes.users import router as users_router
from rhetorica_api.app.routes.workspace_skills import router as workspace_skills_router
from rhetorica_api.app.settings import settings

configure_logging(
    service_name="api",
    environment=settings.app_env,
    log_level=settings.log_level,
    json_logs=settings.log_json,
)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "api.startup.begin",
        mattermost_listener_enabled=settings.mattermost_listener_enabled,
        run_migrations_on_startup=settings.run_migrations_on_startup,
    )
    if settings.run_migrations_on_startup:
        run_migrations()
        logger.info("api.startup.migrations_completed")
    seed_local_admin_account()
    logger.info("api.startup.auth_seed_completed")
    mattermost_supervisor = None
    if settings.mattermost_listener_enabled:
        mattermost_supervisor = MattermostListenerSupervisor()
        mattermost_supervisor.start()
        logger.info("api.startup.mattermost_supervisor_started")
    try:
        yield
    finally:
        if mattermost_supervisor is not None:
            await mattermost_supervisor.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    install_auth(app)
    install_request_logging(app)

    @app.get("/healthz", tags=["health"])
    def healthcheck() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(sql_guard_router)
    app.include_router(execution_router)
    app.include_router(connectors_router)
    app.include_router(llm_connectors_router)
    app.include_router(llm_capabilities_router)
    app.include_router(mattermost_connectors_router)
    app.include_router(openmetadata_connectors_router)
    app.include_router(confluence_connectors_router)
    app.include_router(jira_connectors_router)
    app.include_router(rag_connectors_router)
    app.include_router(workspace_skills_router)
    app.include_router(rag_insights_review_router)
    app.include_router(scenarios_router)
    app.include_router(analysis_exports_router)
    app.include_router(chat_router)
    app.include_router(chat_stream_router)

    # TODO: Remove feature flag guard once MCP is production-ready
    # TODO: Add MCP_ENABLED=false to disable MCP routes in production if needed
    if settings.mcp_enabled:
        app.include_router(mcp_router)

    return app


app = create_app()
