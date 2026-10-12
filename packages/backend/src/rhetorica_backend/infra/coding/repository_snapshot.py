"""Trusted GitLab archive preparation with no repository-supplied execution."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.contracts.coding import CodingRun, ExecutionPrincipal
from rhetorica_backend.domain.chat.gitlab import GitLabService
from rhetorica_backend.infra.coding.archives import ArchivePolicy, SafeArchive


@dataclass(frozen=True)
class PreparedRepositorySnapshot:
    workspace_id: str
    owner_user_id: str
    run_id: str
    base_sha: str
    digest: str
    payload: bytes
    paths: tuple[str, ...]


class TrustedRepositorySnapshotService:
    def __init__(
        self,
        *,
        gitlab: GitLabService | None = None,
        policy: ArchivePolicy | None = None,
    ) -> None:
        self._gitlab = gitlab or GitLabService()
        self._policy = policy or ArchivePolicy()

    def prepare(
        self,
        *,
        connection: GitLabConnectionConfig,
        run: CodingRun,
        principal: ExecutionPrincipal,
    ) -> PreparedRepositorySnapshot:
        self._require_scope(run=run, principal=principal)
        source = self._gitlab.download_repository_archive(
            connection,
            project_path=run.repository.project_id,
            commit_sha=run.repository.base_sha,
            max_bytes=self._policy.max_total_bytes,
        )
        policy = self._source_archive_policy(connection)
        SafeArchive.validate(source, policy=policy)
        payload, paths = self._normalize_archive(source=source, base_sha=run.repository.base_sha)
        SafeArchive.validate(payload, policy=policy)
        return PreparedRepositorySnapshot(
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            run_id=run.id,
            base_sha=run.repository.base_sha,
            digest=hashlib.sha256(payload).hexdigest(),
            payload=payload,
            paths=paths,
        )

    def _source_archive_policy(self, connection: GitLabConnectionConfig) -> ArchivePolicy:
        return ArchivePolicy(
            max_files=connection.coding_archive_max_files,
            max_file_bytes=self._policy.max_file_bytes,
            max_total_bytes=self._policy.max_total_bytes,
        )

    def _normalize_archive(self, *, source: bytes, base_sha: str) -> tuple[bytes, tuple[str, ...]]:
        output = io.BytesIO()
        paths: list[str] = []
        with (
            tarfile.open(fileobj=io.BytesIO(source), mode="r:*") as archive,
            gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as normalized,
        ):
            regular = [member for member in archive.getmembers() if member.isfile()]
            prefix = self._common_archive_prefix(member.name for member in regular)
            for member in regular:
                relative = self._strip_prefix(member.name, prefix)
                if not relative:
                    continue
                path = SafeArchive.normalize_path(relative)
                if path == ".git" or path.startswith(".git/"):
                    continue
                source_file = archive.extractfile(member)
                if source_file is None:
                    continue
                data = source_file.read(self._policy.max_file_bytes + 1)
                if len(data) > self._policy.max_file_bytes:
                    raise ValueError("repository_file_size_exceeded")
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o644 | (0o111 if member.mode & 0o111 else 0)
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                normalized.addfile(info, io.BytesIO(data))
                paths.append(path)
            manifest = json.dumps(
                {
                    "base_sha": base_sha,
                    "hooks": "disabled",
                    "submodules": "disabled",
                    "lfs": "disabled",
                    "setup_scripts": "disabled",
                },
                sort_keys=True,
            ).encode()
            info = tarfile.TarInfo(".rhetorica/base.json")
            info.size = len(manifest)
            info.mode = 0o444
            info.mtime = 0
            normalized.addfile(info, io.BytesIO(manifest))
            paths.append(".rhetorica/base.json")
        return output.getvalue(), tuple(sorted(paths))

    @staticmethod
    def _common_archive_prefix(paths: Iterable[str]) -> str:
        values = [PurePosixPath(path).parts for path in paths]
        if not values or not values[0]:
            return ""
        first = values[0][0]
        return first if all(parts and parts[0] == first for parts in values) else ""

    @staticmethod
    def _strip_prefix(path: str, prefix: str) -> str:
        if not prefix:
            return path
        marker = f"{prefix}/"
        return path[len(marker) :] if path.startswith(marker) else path

    @staticmethod
    def _require_scope(*, run: CodingRun, principal: ExecutionPrincipal) -> None:
        if (
            run.id != principal.run_id
            or run.workspace_id != principal.workspace_id
            or run.owner_user_id != principal.actor_user_id
        ):
            raise PermissionError("repository_snapshot_scope_mismatch")
