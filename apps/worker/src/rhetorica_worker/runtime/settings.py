from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    app_env: str = "dev"
    app_version: str = "0.1.0"
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
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    database_url: str = Field(
        default="sqlite:///./.rhetorica_control_plane.db",
        validation_alias="DATABASE_URL",
    )
    s3_endpoint: str = Field(default="http://minio:9000", validation_alias="S3_ENDPOINT")
    s3_access_key: str = Field(default="minio", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minio123", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="rhetorica", validation_alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", validation_alias="S3_REGION")
    s3_secure: bool = Field(default=False, validation_alias="S3_SECURE")
    file_staged_ttl_hours: int = Field(
        default=24, ge=1, le=168, validation_alias="FILE_STAGED_TTL_HOURS"
    )
    file_sql_export_max_rows: int = Field(
        default=1_048_575,
        ge=1,
        le=1_048_575,
        validation_alias="FILE_SQL_EXPORT_MAX_ROWS",
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

    model_config = SettingsConfigDict(env_prefix="WORKER_", extra="ignore")


settings = WorkerSettings()
