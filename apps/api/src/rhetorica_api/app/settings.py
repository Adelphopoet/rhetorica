from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    app_name: str = "rhetorica-api"
    app_env: str = "dev"
    app_version: str = "0.1.0"
    auth_enabled: bool = Field(default=True, validation_alias="AUTH_ENABLED")
    auth_default_workspace_id: str = Field(
        default="default",
        validation_alias="AUTH_DEFAULT_WORKSPACE_ID",
    )
    auth_jwt_secret: str = Field(
        default="change-me-rhetorica-dev-jwt-secret-0000000000000000",
        validation_alias="AUTH_JWT_SECRET",
    )
    auth_jwt_algorithm: str = Field(default="HS256", validation_alias="AUTH_JWT_ALGORITHM")
    auth_jwt_ttl_minutes: int = Field(default=720, validation_alias="AUTH_JWT_TTL_MINUTES")
    auth_cookie_name: str = Field(
        default="rhetorica_access_token",
        validation_alias="AUTH_COOKIE_NAME",
    )
    auth_cookie_secure: bool = Field(default=False, validation_alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite: str = Field(default="lax", validation_alias="AUTH_COOKIE_SAMESITE")
    auth_local_admin_login: str = Field(default="", validation_alias="AUTH_LOCAL_ADMIN_LOGIN")
    auth_local_admin_password: str = Field(
        default="",
        validation_alias="AUTH_LOCAL_ADMIN_PASSWORD",
    )
    auth_local_admin_display_name: str = Field(
        default="Platform Admin",
        validation_alias="AUTH_LOCAL_ADMIN_DISPLAY_NAME",
    )
    auth_bootstrap_enabled: bool = Field(
        default=False,
        validation_alias="AUTH_BOOTSTRAP_ENABLED",
    )
    auth_bootstrap_login_secret: str = Field(
        default="",
        validation_alias="AUTH_BOOTSTRAP_LOGIN_SECRET",
    )
    log_level: str | None = Field(default=None, validation_alias="LOG_LEVEL")
    log_json: bool | None = Field(default=None, validation_alias="LOG_JSON")
    log_llm_content: bool | None = Field(
        default=None,
        validation_alias="LOG_LLM_CONTENT",
    )
    database_url: str = Field(
        default="sqlite:///./.rhetorica_control_plane.db",
        validation_alias="DATABASE_URL",
    )
    run_migrations_on_startup: bool = Field(
        default=False,
        validation_alias="RUN_MIGRATIONS_ON_STARTUP",
    )
    celery_broker_url: str = Field(
        default="redis://redis:6379/0",
        validation_alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://redis:6379/1",
        validation_alias="CELERY_RESULT_BACKEND",
    )
    mcp_enabled: bool = Field(
        default=True,
        validation_alias="MCP_ENABLED",
        description="Enable MCP server endpoints for AI agent connectivity",
    )
    mattermost_listener_enabled: bool = Field(
        default=True,
        validation_alias="MATTERMOST_LISTENER_ENABLED",
    )
    mattermost_listener_worker_count: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="MATTERMOST_LISTENER_WORKER_COUNT",
    )
    mattermost_listener_queue_size: int = Field(
        default=100,
        ge=1,
        le=10_000,
        validation_alias="MATTERMOST_LISTENER_QUEUE_SIZE",
    )

    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")


settings = ApiSettings()
