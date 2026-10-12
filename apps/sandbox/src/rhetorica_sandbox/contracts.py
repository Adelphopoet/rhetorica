from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SandboxInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    download_url: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20_000)
    inputs: list[SandboxInputModel] = Field(default_factory=list, max_length=6)
    operation_id: str | None = Field(default=None, min_length=1, max_length=80)


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: SandboxInputModel
