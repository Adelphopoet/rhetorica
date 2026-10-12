"""Shared environment settings for API admission and the Temporal worker."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScenarioSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    scenario_workflows_enabled: bool = False
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "rhetorica-scenarios-v1"
    temporal_tls_ca: str = ""
    temporal_tls_cert: str = ""
    temporal_tls_key: str = ""
    temporal_tls_server_name: str = ""
    scenario_worker_concurrency: int = Field(default=4, ge=1, le=100)
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
    sandbox_url: str = Field(default="http://sandbox:8090", validation_alias="SANDBOX_URL")
    sandbox_internal_token: str = Field(
        default="change-me-sandbox-internal-token",
        validation_alias="SANDBOX_INTERNAL_TOKEN",
    )
