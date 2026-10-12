"""Typed file artifact domain values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import BinaryIO


class ArtifactSource(StrEnum):
    USER_UPLOAD = "user_upload"
    SANDBOX_OUTPUT = "sandbox_output"
    SQL_RESULT = "sql_result"


class ArtifactStatus(StrEnum):
    RESERVED = "reserved"
    STAGED = "staged"
    READY = "ready"
    DELETING = "deleting"
    FAILED = "failed"


@dataclass(frozen=True)
class Artifact:
    id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str | None
    storage_key: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    source: ArtifactSource
    status: ArtifactStatus
    source_artifact_ids: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StoredObject:
    """Streaming object returned by storage; caller owns body.close()."""

    body: BinaryIO
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class SandboxInput:
    artifact_id: str
    filename: str
    download_url: str
    sha256: str


@dataclass(frozen=True)
class SandboxOutput:
    output_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SandboxRunResult:
    session_id: str
    exit_code: int
    stdout: str
    stderr: str
    outputs: tuple[SandboxOutput, ...] = field(default_factory=tuple)
    timed_out: bool = False
    oom_killed: bool = False
