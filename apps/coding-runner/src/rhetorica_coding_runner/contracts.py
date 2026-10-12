from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rhetorica_backend.contracts.coding import (
    CODING_OPERATION_ARGUMENT_MAX_CHARS,
    DbtRunRequest,
    DbtRuntimeProfile,
)


class RunnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileInput(RunnerRequest):
    artifact_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    download_url: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileExecuteRequest(RunnerRequest):
    code: str = Field(min_length=1, max_length=20_000)
    inputs: list[FileInput] = Field(default_factory=list, max_length=6)
    operation_id: str | None = Field(default=None, min_length=1, max_length=80)


class FileInspectRequest(RunnerRequest):
    input: FileInput


class ExecuteRequest(RunnerRequest):
    operation: Literal[
        "read_file",
        "search",
        "apply_patch",
        "run_command",
        "prepare_dependencies",
        "run_dbt",
    ]
    arguments: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1, le=3600)
    dbt_request: DbtRunRequest | None = None
    dbt_runtime_profile: DbtRuntimeProfile | None = None

    @field_validator("arguments")
    @classmethod
    def bound_arguments(
        cls, value: dict[str, str | int | bool | list[str]]
    ) -> dict[str, str | int | bool | list[str]]:
        if len(value) > 20:
            raise ValueError("too_many_operation_arguments")
        if any(len(str(item)) > CODING_OPERATION_ARGUMENT_MAX_CHARS for item in value.values()):
            raise ValueError("operation_argument_too_large")
        return value

    @model_validator(mode="after")
    def validate_dbt_payload(self) -> ExecuteRequest:
        has_dbt_payload = self.dbt_request is not None or self.dbt_runtime_profile is not None
        if self.operation == "run_dbt":
            if self.dbt_request is None or self.dbt_runtime_profile is None:
                raise ValueError("run_dbt_requires_trusted_profile")
            if self.arguments:
                raise ValueError("run_dbt_arguments_forbidden")
        elif has_dbt_payload:
            raise ValueError("dbt_payload_requires_run_dbt")
        return self
