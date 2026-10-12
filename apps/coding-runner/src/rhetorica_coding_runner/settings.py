from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CodingRunnerSettings(BaseSettings):
    task_token: str = Field(
        validation_alias=AliasChoices(
            "EXECUTION_RUNTIME_INTERNAL_TOKEN", "CODING_RUNNER_TASK_TOKEN"
        )
    )
    workspace_root: Path = Field(default=Path("/workspace/repository"))
    scratch_root: Path = Field(default=Path("/scratch"))
    max_snapshot_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        validation_alias=AliasChoices(
            "EXECUTION_RUNTIME_MAX_SNAPSHOT_BYTES", "CODING_RUNNER_MAX_SNAPSHOT_BYTES"
        ),
    )
    max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_files: int = Field(default=20_000, ge=1)
    max_output_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        validation_alias=AliasChoices(
            "EXECUTION_RUNTIME_MAX_OUTPUT_BYTES", "CODING_RUNNER_MAX_OUTPUT_BYTES"
        ),
    )
    pid_limit: int = Field(default=128, ge=16, validation_alias="CODING_RUNNER_PID_LIMIT")
    process_memory_limit_bytes: int | None = Field(
        default=None, ge=128 * 1024 * 1024, validation_alias="EXECUTION_RUNTIME_MEMORY_LIMIT_BYTES"
    )
    sessions_root: Path = Field(
        default=Path("/workspace/file-sessions"),
        validation_alias="EXECUTION_RUNTIME_SESSIONS_ROOT",
    )
    process_timeout_seconds: int = Field(
        default=60, ge=1, le=300, validation_alias="EXECUTION_RUNTIME_PROCESS_TIMEOUT_SECONDS"
    )
    session_ttl_minutes: int = Field(
        default=20, ge=1, le=120, validation_alias="EXECUTION_RUNTIME_SESSION_TTL_MINUTES"
    )
    cleanup_interval_seconds: int = Field(
        default=60, ge=5, le=300, validation_alias="EXECUTION_RUNTIME_CLEANUP_INTERVAL_SECONDS"
    )
    max_input_bytes: int = Field(
        default=25 * 1024 * 1024, ge=1, validation_alias="EXECUTION_RUNTIME_MAX_INPUT_BYTES"
    )
    max_session_output_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        validation_alias="EXECUTION_RUNTIME_MAX_SESSION_OUTPUT_BYTES",
    )
    max_workspace_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        validation_alias="EXECUTION_RUNTIME_MAX_WORKSPACE_BYTES",
    )
    max_log_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        le=1024 * 1024,
        validation_alias="EXECUTION_RUNTIME_MAX_LOG_BYTES",
    )
    max_output_files: int = Field(
        default=100, ge=1, le=1000, validation_alias="EXECUTION_RUNTIME_MAX_OUTPUT_FILES"
    )
    max_session_output_files: int = Field(
        default=200,
        ge=1,
        le=2000,
        validation_alias="EXECUTION_RUNTIME_MAX_SESSION_OUTPUT_FILES",
    )
    allowed_dependency_sources: str = Field(
        default="", validation_alias="CODING_RUNNER_DEPENDENCY_SOURCES"
    )
    phase: Literal["runtime", "dependencies"] = Field(
        default="runtime", validation_alias="CODING_RUNNER_PHASE"
    )
    software_manifest_path: Path = Path("/opt/software-manifest.json")

    @model_validator(mode="after")
    def validate_file_limits(self) -> CodingRunnerSettings:
        if self.max_session_output_bytes < self.max_output_bytes:
            raise ValueError("session output limit must cover one output")
        if self.max_workspace_bytes < max(self.max_input_bytes, self.max_session_output_bytes):
            raise ValueError("workspace limit must cover configured inputs and outputs")
        if self.max_session_output_files < self.max_output_files:
            raise ValueError("session output file limit must cover one run")
        return self

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
