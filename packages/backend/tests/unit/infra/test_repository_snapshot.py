from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest
from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.contracts.coding import CodingRun, ExecutionPrincipal
from rhetorica_backend.infra.coding.archives import UnsafeArchiveError
from rhetorica_backend.infra.coding.repository_snapshot import TrustedRepositorySnapshotService


def _archive() -> bytes:
    output = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        root = tarfile.TarInfo("dwh-main")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for path in ("dwh-main/trino/dbt_project.yml", "dwh-main/trino/packages.yml"):
            payload = b"name: test\n"
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


class _GitLab:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive

    def download_repository_archive(self, *_args, **_kwargs) -> bytes:
        return self.archive


def _run() -> CodingRun:
    fixture = Path(__file__).parents[2] / "fixtures" / "coding" / "success.json"
    return CodingRun.model_validate_json(fixture.read_text(encoding="utf-8"))


def _principal() -> ExecutionPrincipal:
    return ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        root_agent_id="manager",
        executing_agent_id="developer",
        parent_agent_id="manager",
        run_id="run-success",
    )


def _connection(*, max_files: int) -> GitLabConnectionConfig:
    return GitLabConnectionConfig(
        base_url="https://gitlab.example.com",
        api_token="token",
        use_when="Coding source snapshot",
        coding_archive_max_files=max_files,
    )


def test_repository_snapshot_uses_connector_archive_entry_limit() -> None:
    service = TrustedRepositorySnapshotService(gitlab=_GitLab(_archive()))

    snapshot = service.prepare(
        connection=_connection(max_files=3), run=_run(), principal=_principal()
    )

    assert snapshot.paths == (".rhetorica/base.json", "trino/dbt_project.yml", "trino/packages.yml")


def test_repository_snapshot_rejects_archive_above_connector_limit() -> None:
    service = TrustedRepositorySnapshotService(gitlab=_GitLab(_archive()))

    with pytest.raises(UnsafeArchiveError, match="archive_file_count_exceeded"):
        service.prepare(connection=_connection(max_files=2), run=_run(), principal=_principal())
