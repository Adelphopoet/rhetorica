"""Narrow ports owned by the file artifacts bounded context."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import BinaryIO, Protocol

from rhetorica_backend.domain.files.models import (
    Artifact,
    ArtifactSource,
    ArtifactStatus,
    SandboxInput,
    SandboxRunResult,
    StoredObject,
)


class SandboxGatewayError(RuntimeError):
    """Typed adapter failure that an LLM can react to without transport leakage."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_recommended: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_recommended = retry_recommended


class ArtifactRepository(Protocol):
    def reserve(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        storage_key: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        source: ArtifactSource,
        source_artifact_ids: Iterable[str] = (),
        user_quota_bytes: int,
        workspace_quota_bytes: int,
    ) -> Artifact: ...

    def finalize_reservation(self, artifact_id: str) -> Artifact: ...

    def cancel_reservation(self, artifact_id: str) -> None: ...

    def create(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        storage_key: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        source: ArtifactSource,
        status: ArtifactStatus,
        source_artifact_ids: Iterable[str] = (),
    ) -> Artifact: ...

    def get_owned(
        self, artifact_id: str, owner_user_id: str, workspace_id: str
    ) -> Artifact | None: ...

    def list_conversation(
        self, *, workspace_id: str, conversation_id: str, owner_user_id: str, limit: int = 100
    ) -> list[Artifact]: ...

    def usage_bytes(self, *, workspace_id: str, owner_user_id: str | None = None) -> int: ...

    def attach_to_message(
        self,
        *,
        artifact_ids: Iterable[str],
        message_id: str,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        relation: str,
    ) -> list[Artifact]: ...

    def delete_staged(
        self, *, artifact_id: str, owner_user_id: str, workspace_id: str
    ) -> Artifact | None: ...

    def mark_failed(self, artifact_id: str) -> None: ...

    def claim_cleanup(self, *, staged_before: datetime, limit: int = 100) -> list[Artifact]: ...

    def finish_cleanup(self, artifact_id: str) -> None: ...

    def retry_cleanup(self, artifact_id: str) -> None: ...


class ObjectStorage(Protocol):
    def upload_fileobj(
        self,
        *,
        key: str,
        fileobj: BinaryIO,
        size_bytes: int,
        media_type: str,
    ) -> None: ...

    def open(self, key: str) -> StoredObject: ...

    def delete(self, key: str) -> None: ...

    def create_download_url(self, key: str, *, expires_seconds: int = 300) -> str: ...


class SandboxGateway(Protocol):
    """Provider-neutral sandbox control plane; deploy adapter may vary."""

    def ensure_session(self, session_id: str) -> str: ...

    def run_python(
        self,
        *,
        session_id: str | None,
        code: str,
        inputs: Iterable[SandboxInput],
        operation_id: str | None = None,
    ) -> SandboxRunResult: ...

    def inspect_file(
        self,
        *,
        session_id: str | None,
        input_file: SandboxInput,
    ) -> dict[str, object]: ...

    def close_session(self, session_id: str) -> None: ...

    def download_output(
        self,
        *,
        session_id: str,
        output_id: str,
        destination: BinaryIO,
        max_bytes: int,
    ) -> tuple[str, str, int]: ...
