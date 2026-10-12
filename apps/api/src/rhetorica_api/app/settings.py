from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    app_name: str = "rhetorica-api"
    app_env: str = "dev"
    app_version: str = "0.1.0"
    rhetorica_public_url: str = Field(
        default="http://localhost:3000",
        validation_alias="RHETORICA_PUBLIC_URL",
    )
    rhetorica_public_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias="RHETORICA_PUBLIC_API_URL",
    )
    auth_enabled: bool = Field(default=True, validation_alias="AUTH_ENABLED")
    auth_default_workspace_id: str = Field(
        default="default",
        validation_alias="AUTH_DEFAULT_WORKSPACE_ID",
    )
    auth_jwt_secret: str = Field(
        default="change-me-rhetorica-dev-jwt-secret-0000000000000000",
        validation_alias="AUTH_JWT_SECRET",
    )
    auth_api_token_pepper: str = Field(
        default="change-me-rhetorica-dev-pat-pepper-0000000000000000",
        validation_alias="AUTH_API_TOKEN_PEPPER",
        description="Dedicated HMAC pepper for Personal Access Tokens",
    )
    auth_oauth_token_pepper: str = Field(
        default="change-me-rhetorica-dev-oauth-pepper-0000000000000000",
        validation_alias="AUTH_OAUTH_TOKEN_PEPPER",
        description="Dedicated HMAC pepper for OAuth secrets",
    )
    auth_oauth_cimd_enabled: bool = Field(
        default=True,
        validation_alias="AUTH_OAUTH_CIMD_ENABLED",
    )
    auth_oauth_transaction_ttl_minutes: int = Field(
        default=10,
        ge=2,
        le=30,
        validation_alias="AUTH_OAUTH_TRANSACTION_TTL_MINUTES",
    )
    auth_oauth_code_ttl_minutes: int = Field(
        default=5,
        ge=1,
        le=10,
        validation_alias="AUTH_OAUTH_CODE_TTL_MINUTES",
    )
    auth_oauth_access_ttl_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        validation_alias="AUTH_OAUTH_ACCESS_TTL_MINUTES",
    )
    auth_oauth_refresh_ttl_days: int = Field(
        default=30,
        ge=1,
        le=90,
        validation_alias="AUTH_OAUTH_REFRESH_TTL_DAYS",
    )
    auth_oauth_dcr_registrations_per_source: int = Field(
        default=30,
        ge=1,
        le=10_000,
        validation_alias="AUTH_OAUTH_DCR_REGISTRATIONS_PER_SOURCE",
    )
    auth_oauth_dcr_max_clients: int = Field(
        default=10_000,
        ge=1,
        le=10_000_000,
        validation_alias="AUTH_OAUTH_DCR_MAX_CLIENTS",
    )
    auth_oauth_record_retention_hours: int = Field(
        default=24,
        ge=1,
        le=24 * 365,
        validation_alias="AUTH_OAUTH_RECORD_RETENTION_HOURS",
    )
    auth_oauth_dcr_inactive_client_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        validation_alias="AUTH_OAUTH_DCR_INACTIVE_CLIENT_RETENTION_DAYS",
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
    auth_keycloak_enabled: bool = Field(default=False, validation_alias="AUTH_KEYCLOAK_ENABLED")
    auth_keycloak_issuer_url: str = Field(
        default="",
        validation_alias="AUTH_KEYCLOAK_ISSUER_URL",
    )
    auth_keycloak_client_id: str = Field(
        default="",
        validation_alias="AUTH_KEYCLOAK_CLIENT_ID",
    )
    auth_keycloak_admin_groups: str = Field(
        default="",
        validation_alias="AUTH_KEYCLOAK_ADMIN_GROUPS",
    )
    auth_keycloak_analyst_groups: str = Field(
        default="",
        validation_alias="AUTH_KEYCLOAK_ANALYST_GROUPS",
    )
    auth_keycloak_require_mapped_group: bool = Field(
        default=True,
        validation_alias="AUTH_KEYCLOAK_REQUIRE_MAPPED_GROUP",
    )
    log_level: str | None = Field(default=None, validation_alias="LOG_LEVEL")
    log_json: bool | None = Field(default=None, validation_alias="LOG_JSON")
    log_llm_content: bool | None = Field(
        default=None,
        validation_alias="LOG_LLM_CONTENT",
    )
    otel_enabled: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str = Field(
        default="",
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    otel_exporter_otlp_insecure: bool = Field(
        default=False,
        validation_alias="OTEL_EXPORTER_OTLP_INSECURE",
    )
    langfuse_enabled: bool = Field(default=False, validation_alias="LANGFUSE_ENABLED")
    database_url: str = Field(
        default="sqlite:///./.rhetorica_control_plane.db",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        validation_alias="REDIS_URL",
    )
    rate_limit_redis_key_prefix: str = Field(
        default="rhetorica:rate-limit:api",
        min_length=1,
        validation_alias="RATE_LIMIT_REDIS_KEY_PREFIX",
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
    s3_endpoint: str = Field(default="http://minio:9000", validation_alias="S3_ENDPOINT")
    s3_access_key: str = Field(default="minio", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minio123", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="rhetorica", validation_alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_secure: bool = Field(default=False, validation_alias="S3_SECURE")
    file_max_input_bytes: int = Field(
        default=25 * 1024 * 1024, validation_alias="FILE_MAX_INPUT_BYTES"
    )
    file_max_files_per_turn: int = Field(
        default=5, ge=1, le=20, validation_alias="FILE_MAX_FILES_PER_TURN"
    )
    file_max_turn_bytes: int = Field(
        default=50 * 1024 * 1024, validation_alias="FILE_MAX_TURN_BYTES"
    )
    file_max_output_bytes: int = Field(
        default=50 * 1024 * 1024, validation_alias="FILE_MAX_OUTPUT_BYTES"
    )
    file_max_session_output_bytes: int = Field(
        default=100 * 1024 * 1024, validation_alias="FILE_MAX_SESSION_OUTPUT_BYTES"
    )
    file_sql_export_max_rows: int = Field(
        default=1_048_575,
        ge=1,
        le=1_048_575,
        validation_alias="FILE_SQL_EXPORT_MAX_ROWS",
    )
    file_sql_export_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        validation_alias="FILE_SQL_EXPORT_MAX_BYTES",
    )
    sql_result_snapshot_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        validation_alias="SQL_RESULT_SNAPSHOT_MAX_BYTES",
    )
    sql_result_snapshot_max_cell_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        validation_alias="SQL_RESULT_SNAPSHOT_MAX_CELL_BYTES",
    )
    sql_result_snapshot_max_row_bytes: int = Field(
        default=16 * 1024 * 1024,
        ge=1024,
        validation_alias="SQL_RESULT_SNAPSHOT_MAX_ROW_BYTES",
    )
    sql_result_preview_rows: int = Field(
        default=500,
        ge=1,
        le=500,
        validation_alias="SQL_RESULT_PREVIEW_ROWS",
    )
    file_user_quota_bytes: int = Field(
        default=1024 * 1024 * 1024, validation_alias="FILE_USER_QUOTA_BYTES"
    )
    file_workspace_quota_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,
        validation_alias="FILE_WORKSPACE_QUOTA_BYTES",
    )
    execution_runtime_url: str = Field(
        default="http://execution-runtime:8091",
        validation_alias="EXECUTION_RUNTIME_URL",
    )
    execution_runtime_internal_token: str = Field(
        default="change-me-execution-runtime-internal-token",
        validation_alias="EXECUTION_RUNTIME_INTERNAL_TOKEN",
    )
    coding_enabled: bool = Field(
        default=False,
        validation_alias="CODING_ENABLED",
        description="Admit new governed coding runs; existing runs may still be drained",
    )
    coding_runner_image_digest: str = Field(
        default="rhetorica-coding-runner@sha256:" + "0" * 64,
        validation_alias="CODING_RUNNER_IMAGE_DIGEST",
    )
    coding_runner_runtime_class_name: str = Field(
        default="runsc", validation_alias="CODING_RUNNER_RUNTIME_CLASS_NAME"
    )
    coding_runner_software_manifest_digest: str = Field(
        default="0" * 64,
        validation_alias="CODING_RUNNER_SOFTWARE_MANIFEST_DIGEST",
    )
    mcp_enabled: bool = Field(
        default=True,
        validation_alias="MCP_ENABLED",
        description="Enable MCP server endpoints for AI agent connectivity",
    )
    mcp_allowed_hosts: str = Field(
        default="127.0.0.1:*,localhost:*,[::1]:*,testserver,api:8000",
        validation_alias="MCP_ALLOWED_HOSTS",
    )
    mcp_allowed_origins: str = Field(
        default="http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
        validation_alias="MCP_ALLOWED_ORIGINS",
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
