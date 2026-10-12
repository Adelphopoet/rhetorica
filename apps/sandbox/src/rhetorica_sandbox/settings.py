from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseSettings):
    internal_token: str = Field(
        default="change-me-sandbox-internal-token",
        validation_alias="SANDBOX_INTERNAL_TOKEN",
    )
    sessions_root: Path = Field(
        default=Path("/var/lib/rhetorica-sandbox/sessions"),
        validation_alias="SANDBOX_SESSIONS_ROOT",
    )
    nsjail_path: str = Field(default="/usr/local/bin/nsjail", validation_alias="NSJAIL_PATH")
    python_path: str = Field(
        default="/opt/rhetorica-venv/bin/python",
        validation_alias="SANDBOX_PYTHON_PATH",
    )
    max_parallel_runs: int = Field(
        default=2, ge=1, le=16, validation_alias="SANDBOX_MAX_PARALLEL_RUNS"
    )
    process_timeout_seconds: int = Field(
        default=60, ge=1, le=300, validation_alias="SANDBOX_PROCESS_TIMEOUT_SECONDS"
    )
    session_ttl_minutes: int = Field(
        default=20, ge=1, le=120, validation_alias="SANDBOX_SESSION_TTL_MINUTES"
    )
    cleanup_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="SANDBOX_CLEANUP_INTERVAL_SECONDS",
    )
    max_input_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        validation_alias="SANDBOX_MAX_INPUT_BYTES",
    )
    max_output_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        validation_alias="SANDBOX_MAX_OUTPUT_BYTES",
    )
    max_session_output_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        validation_alias="SANDBOX_MAX_SESSION_OUTPUT_BYTES",
    )
    max_workspace_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        validation_alias="SANDBOX_MAX_WORKSPACE_BYTES",
    )
    max_tmp_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=4 * 1024 * 1024,
        validation_alias="SANDBOX_MAX_TMP_BYTES",
    )
    max_log_bytes: int = Field(
        default=64 * 1024,
        ge=1024,
        le=1024 * 1024,
        validation_alias="SANDBOX_MAX_LOG_BYTES",
    )
    max_output_files: int = Field(
        default=100,
        ge=1,
        le=1000,
        validation_alias="SANDBOX_MAX_OUTPUT_FILES",
    )
    max_session_output_files: int = Field(
        default=200,
        ge=1,
        le=2000,
        validation_alias="SANDBOX_MAX_SESSION_OUTPUT_FILES",
    )
    memory_limit_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=128 * 1024 * 1024,
        validation_alias="SANDBOX_MEMORY_LIMIT_BYTES",
    )
    pids_limit: int = Field(default=64, ge=16, le=256, validation_alias="SANDBOX_PIDS_LIMIT")
    cpu_ms_per_second: int = Field(
        default=1000,
        ge=100,
        le=8000,
        validation_alias="SANDBOX_CPU_MS_PER_SECOND",
    )
    manage_legacy_cgroup_parents: bool = Field(
        default=True,
        validation_alias="SANDBOX_MANAGE_LEGACY_CGROUP_PARENTS",
    )
    pod_uid: str = Field(default="", validation_alias="POD_UID")

    @property
    def cgroup_parent(self) -> str:
        suffix = self.pod_uid.replace("-", "_")
        return f"NSJAIL_{suffix}" if suffix else "NSJAIL"

    @model_validator(mode="after")
    def _validate_related_limits(self) -> SandboxSettings:
        if self.max_session_output_bytes < self.max_output_bytes:
            raise ValueError("session output limit must cover one output")
        if self.max_workspace_bytes < max(
            self.max_input_bytes,
            self.max_session_output_bytes,
        ):
            raise ValueError("workspace limit must cover configured inputs and outputs")
        if self.memory_limit_bytes < self.max_output_bytes:
            raise ValueError("memory limit must cover one output")
        if self.max_tmp_bytes > self.memory_limit_bytes:
            raise ValueError("tmp limit must not exceed the process memory limit")
        if self.max_session_output_files < self.max_output_files:
            raise ValueError("session output file limit must cover one run")
        return self

    model_config = SettingsConfigDict(extra="ignore")


settings = SandboxSettings()
