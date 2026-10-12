"""File artifact use cases; transport and storage implementations stay outside."""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from time import perf_counter
from typing import BinaryIO
from urllib.parse import quote
from uuid import uuid4

from rhetorica_backend.contracts.files import FileArtifactDetail, FileArtifactRef
from rhetorica_backend.domain.files.models import (
    Artifact,
    ArtifactSource,
    ArtifactStatus,
    SandboxInput,
    StoredObject,
)
from rhetorica_backend.domain.files.policy import ArtifactPolicy
from rhetorica_backend.domain.files.ports import ArtifactRepository, ObjectStorage
from rhetorica_backend.domain.files.telemetry import FileTelemetry, NoopFileTelemetry


class ArtifactNotFoundError(LookupError):
    pass


class ArtifactConflictError(ValueError):
    pass


class ArtifactService:
    """Coordinates immutable artifacts without buffering complete files in memory."""

    _HASH_CHUNK_BYTES = 1024 * 1024

    def __init__(
        self,
        *,
        repository: ArtifactRepository,
        storage: ObjectStorage,
        policy: ArtifactPolicy,
        telemetry: FileTelemetry | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._policy = policy
        self._telemetry = telemetry or NoopFileTelemetry()

    def stage_upload(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        filename: str,
        declared_media_type: str | None,
        fileobj: BinaryIO,
    ) -> Artifact:
        started = perf_counter()
        size_bytes = 0
        outcome = "error"
        try:
            artifact = self._stage_upload(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                filename=filename,
                declared_media_type=declared_media_type,
                fileobj=fileobj,
            )
        except (ArtifactConflictError, ValueError):
            outcome = "rejected"
            raise
        else:
            size_bytes = artifact.size_bytes
            outcome = "ok"
            return artifact
        finally:
            self._telemetry.record(
                operation="upload",
                outcome=outcome,
                duration_seconds=perf_counter() - started,
                bytes_count=size_bytes,
            )

    def _stage_upload(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        filename: str,
        declared_media_type: str | None,
        fileobj: BinaryIO,
    ) -> Artifact:
        safe_filename = self._policy.normalize_filename(filename)
        size_bytes, sha256 = self._measure(fileobj)
        self._policy.validate_input_size(size_bytes)
        media_type = self._policy.inspect(
            filename=safe_filename,
            declared_media_type=declared_media_type,
            fileobj=fileobj,
        )
        artifact_id = str(uuid4())
        storage_key = self._storage_key(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            filename=safe_filename,
        )
        return self._reserve_upload_and_finalize(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            storage_key=storage_key,
            filename=safe_filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            source=ArtifactSource.USER_UPLOAD,
            source_artifact_ids=(),
            fileobj=fileobj,
        )

    def store_generated(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        filename: str,
        declared_media_type: str,
        fileobj: BinaryIO,
        source: ArtifactSource,
        source_artifact_ids: list[str] | None = None,
        max_size_bytes: int | None = None,
        artifact_id: str | None = None,
    ) -> Artifact:
        started = perf_counter()
        size_bytes = 0
        outcome = "error"
        try:
            artifact = self._store_generated(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                filename=filename,
                declared_media_type=declared_media_type,
                fileobj=fileobj,
                source=source,
                source_artifact_ids=source_artifact_ids,
                max_size_bytes=max_size_bytes,
                artifact_id=artifact_id,
            )
        except (ArtifactConflictError, ValueError):
            outcome = "rejected"
            raise
        else:
            size_bytes = artifact.size_bytes
            outcome = "ok"
            return artifact
        finally:
            self._telemetry.record(
                operation="publish",
                outcome=outcome,
                duration_seconds=perf_counter() - started,
                bytes_count=size_bytes,
            )

    def _store_generated(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        filename: str,
        declared_media_type: str,
        fileobj: BinaryIO,
        source: ArtifactSource,
        source_artifact_ids: list[str] | None = None,
        max_size_bytes: int | None = None,
        artifact_id: str | None = None,
    ) -> Artifact:
        if source is ArtifactSource.USER_UPLOAD:
            raise ValueError("Generated artifact source is required")
        safe_filename = self._policy.normalize_filename(filename)
        size_bytes, sha256 = self._measure(fileobj)
        output_limit = max_size_bytes or self._policy.config.max_output_bytes
        if size_bytes <= 0 or size_bytes > output_limit:
            raise ArtifactConflictError("Generated file exceeds output limit")
        media_type = self._policy.inspect(
            filename=safe_filename,
            declared_media_type=declared_media_type,
            fileobj=fileobj,
            trusted_generated=source is ArtifactSource.SQL_RESULT,
        )
        if artifact_id:
            existing = self._repository.get_owned(artifact_id, owner_user_id, workspace_id)
            if existing is not None:
                if (
                    existing.workspace_id != workspace_id
                    or existing.conversation_id != conversation_id
                    or existing.sha256 != sha256
                    or existing.filename != safe_filename
                ):
                    raise ArtifactConflictError(
                        "Artifact operation ID was reused with different content"
                    )
                if existing.status in {ArtifactStatus.READY, ArtifactStatus.STAGED}:
                    return existing
                if existing.status is not ArtifactStatus.RESERVED:
                    raise ArtifactConflictError("Artifact operation is unavailable")
                self._storage.upload_fileobj(
                    key=existing.storage_key,
                    fileobj=fileobj,
                    size_bytes=size_bytes,
                    media_type=media_type,
                )
                return self._repository.finalize_reservation(artifact_id)
        artifact_id = artifact_id or str(uuid4())
        storage_key = self._storage_key(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            filename=safe_filename,
        )
        return self._reserve_upload_and_finalize(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            storage_key=storage_key,
            filename=safe_filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            source=source,
            source_artifact_ids=source_artifact_ids or (),
            fileobj=fileobj,
        )

    def validate_turn_inputs(
        self,
        *,
        artifact_ids: list[str],
        workspace_id: str,
        conversation_id: str,
        owner_user_id: str,
    ) -> list[Artifact]:
        artifacts = [
            self.get_owned(
                artifact_id=artifact_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            for artifact_id in dict.fromkeys(artifact_ids)
        ]
        if len(artifacts) != len(artifact_ids):
            raise ArtifactConflictError("Duplicate attachment IDs are not allowed")
        if any(artifact.conversation_id != conversation_id for artifact in artifacts):
            raise ArtifactConflictError("Artifact belongs to another conversation")
        self._policy.validate_turn([artifact.size_bytes for artifact in artifacts])
        return artifacts

    def get_owned(self, *, artifact_id: str, owner_user_id: str, workspace_id: str) -> Artifact:
        artifact = self._repository.get_owned(artifact_id, owner_user_id, workspace_id)
        if artifact is None or artifact.status in {
            ArtifactStatus.RESERVED,
            ArtifactStatus.DELETING,
            ArtifactStatus.FAILED,
        }:
            raise ArtifactNotFoundError(artifact_id)
        return artifact

    def open_content(
        self, *, artifact_id: str, owner_user_id: str, workspace_id: str
    ) -> tuple[Artifact, StoredObject]:
        artifact = self.get_owned(
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return artifact, self._storage.open(artifact.storage_key)

    def delete_staged(self, *, artifact_id: str, owner_user_id: str, workspace_id: str) -> bool:
        artifact = self._repository.delete_staged(
            artifact_id=artifact_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        if artifact is None:
            raise ArtifactConflictError("Only unlinked staged uploads can be deleted")
        try:
            self._storage.delete(artifact.storage_key)
            self._repository.finish_cleanup(artifact.id)
        except Exception:
            self._repository.retry_cleanup(artifact.id)
            raise
        return True

    def attach_inputs(
        self,
        *,
        artifact_ids: list[str],
        workspace_id: str,
        message_id: str,
        conversation_id: str,
        owner_user_id: str,
    ) -> list[Artifact]:
        self.validate_turn_inputs(
            artifact_ids=artifact_ids,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        return self._repository.attach_to_message(
            artifact_ids=artifact_ids,
            message_id=message_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            relation="input",
        )

    def list_conversation(
        self, *, workspace_id: str, conversation_id: str, owner_user_id: str, limit: int = 100
    ) -> list[Artifact]:
        return self._repository.list_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            limit=limit,
        )

    @property
    def max_output_bytes(self) -> int:
        return self._policy.config.max_output_bytes

    @property
    def max_session_output_bytes(self) -> int:
        return self._policy.config.max_session_output_bytes

    def sandbox_input(self, artifact: Artifact) -> SandboxInput:
        return SandboxInput(
            artifact_id=artifact.id,
            filename=artifact.filename,
            download_url=self._storage.create_download_url(artifact.storage_key),
            sha256=artifact.sha256,
        )

    def cleanup(self, *, staged_ttl_hours: int = 24, limit: int = 100) -> int:
        started = perf_counter()
        claimed = self._repository.claim_cleanup(
            staged_before=datetime.now(tz=UTC) - timedelta(hours=staged_ttl_hours),
            limit=limit,
        )
        cleaned = 0
        for artifact in claimed:
            try:
                self._storage.delete(artifact.storage_key)
                self._repository.finish_cleanup(artifact.id)
                cleaned += 1
            except Exception:
                self._repository.retry_cleanup(artifact.id)
        self._telemetry.record(
            operation="cleanup",
            outcome="ok" if cleaned == len(claimed) else "partial",
            duration_seconds=perf_counter() - started,
            count=cleaned,
        )
        return cleaned

    @staticmethod
    def to_ref(artifact: Artifact) -> FileArtifactRef:
        return FileArtifactRef(
            id=artifact.id,
            filename=artifact.filename,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            kind=artifact.source.value,
            sha256=artifact.sha256,
        )

    @classmethod
    def to_detail(cls, artifact: Artifact) -> FileArtifactDetail:
        return FileArtifactDetail(
            **cls.to_ref(artifact).model_dump(),
            workspace_id=artifact.workspace_id,
            conversation_id=artifact.conversation_id,
            status=artifact.status.value,
            source_artifact_ids=list(artifact.source_artifact_ids),
            created_at=artifact.created_at.isoformat() if artifact.created_at else "",
        )

    @staticmethod
    def content_disposition(filename: str) -> str:
        suffix = PurePath(filename).suffix or ".bin"
        stem = re.sub(
            r"[^a-z0-9_-]+",
            "-",
            PurePath(filename).stem.lower().encode("ascii", "ignore").decode("ascii"),
        ).strip("-_")
        fallback = f"{stem or 'artifact'}{suffix}"
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"

    @classmethod
    def _measure(cls, fileobj: BinaryIO) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        fileobj.seek(0)
        while chunk := fileobj.read(cls._HASH_CHUNK_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
        fileobj.seek(0)
        return size_bytes, digest.hexdigest()

    def _reserve_upload_and_finalize(
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
        source_artifact_ids: tuple[str, ...] | list[str],
        fileobj: BinaryIO,
    ) -> Artifact:
        self._repository.reserve(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            storage_key=storage_key,
            filename=filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            source=source,
            source_artifact_ids=source_artifact_ids,
            user_quota_bytes=self._policy.config.user_quota_bytes,
            workspace_quota_bytes=self._policy.config.workspace_quota_bytes,
        )
        try:
            fileobj.seek(0)
            self._storage.upload_fileobj(
                key=storage_key,
                fileobj=fileobj,
                size_bytes=size_bytes,
                media_type=media_type,
            )
            return self._repository.finalize_reservation(artifact_id)
        except Exception:
            try:
                self._storage.delete(storage_key)
            except Exception:
                with suppress(Exception):
                    self._repository.retry_cleanup(artifact_id)
            else:
                self._repository.cancel_reservation(artifact_id)
            raise

    @staticmethod
    def _storage_key(
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        artifact_id: str,
        filename: str,
    ) -> str:
        extension = PurePath(filename).suffix.lower()
        return (
            f"workspaces/{workspace_id}/users/{owner_user_id}/"
            f"conversations/{conversation_id}/{artifact_id}/content{extension}"
        )
