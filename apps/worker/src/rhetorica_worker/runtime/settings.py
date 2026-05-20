from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    app_env: str = "dev"
    log_level: str | None = Field(default=None, validation_alias="LOG_LEVEL")
    log_json: bool | None = Field(default=None, validation_alias="LOG_JSON")
    log_llm_content: bool | None = Field(
        default=None,
        validation_alias="LOG_LLM_CONTENT",
    )
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    database_url: str = Field(
        default="sqlite:///./.rhetorica_control_plane.db",
        validation_alias="DATABASE_URL",
    )

    model_config = SettingsConfigDict(env_prefix="WORKER_", extra="ignore")


settings = WorkerSettings()
