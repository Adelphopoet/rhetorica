from __future__ import annotations

from pathlib import Path

import pytest
from rhetorica_backend.contracts.coding import UpsertEnvironmentProfileRequest
from rhetorica_backend.infra.postgres.coding_environment_repository import (
    CodingEnvironmentConflictError,
    CodingEnvironmentRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _request(*, revision: int, cpu: str = "1") -> UpsertEnvironmentProfileRequest:
    return UpsertEnvironmentProfileRequest(
        id="gvisor-small",
        expected_revision=revision,
        max_runtime_seconds=1200,
        cpu_limit=cpu,
        memory_limit="2Gi",
        disk_limit_bytes=5 * 1024**3,
        pid_limit=128,
        output_limit_bytes=64 * 1024**2,
        dependency_sources=["https://packages.example/simple"],
    )


def test_environment_profile_revisions_are_immutable(tmp_path: Path) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/coding-environments.db")
    runtime.create_schema()
    repository = CodingEnvironmentRepository(runtime)
    identity = {
        "actor_user_id": "admin-1",
        "image_digest": "runner@sha256:" + "a" * 64,
        "runtime_class_name": "runsc",
        "tool_versions": {"python": "3.12.12"},
        "software_manifest_digest": "b" * 64,
    }

    first = repository.replace(request=_request(revision=0), **identity)
    second = repository.replace(request=_request(revision=1, cpu="2000m"), **identity)

    assert first.revision == 1
    assert second.revision == 2
    assert repository.get(profile_id="gvisor-small", revision=1).cpu_limit == "1"  # type: ignore[union-attr]
    latest = repository.list_latest()
    assert [(item.id, item.revision, item.cpu_limit) for item in latest] == [
        (second.id, second.revision, second.cpu_limit)
    ]
    with pytest.raises(CodingEnvironmentConflictError) as error:
        repository.replace(request=_request(revision=1), **identity)
    assert error.value.current_revision == 2
