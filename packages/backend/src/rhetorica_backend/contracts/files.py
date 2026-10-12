"""Public, storage-neutral file artifact contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactKind = Literal["user_upload", "sandbox_output", "sql_result"]


class FileArtifactRef(BaseModel):
    """Safe file reference exposed to transports and clients."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileArtifactDetail(FileArtifactRef):
    """Owned artifact metadata returned by the files API."""

    workspace_id: str
    conversation_id: str | None = None
    status: Literal["staged", "ready", "deleting", "failed"]
    source_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str
