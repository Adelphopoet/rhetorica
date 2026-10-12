from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from rhetorica_backend.contracts.coding import SandboxLeaseStatus, SandboxTaskLease
from rhetorica_backend.infra.postgres.coding_sandbox_repository import (
    CodingSandboxLeaseRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _lease(*, lease_id: str = "lease-1", job_name: str = "coding-lease-1") -> SandboxTaskLease:
    now = datetime.now(tz=UTC)
    return SandboxTaskLease(
        id=lease_id,
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id="run-1",
        task_id="task-1",
        environment_profile_id="dbt",
        environment_profile_revision=1,
        image_digest="registry/coding@sha256:" + "a" * 64,
        runtime_class_name="runsc",
        job_name=job_name,
        status=SandboxLeaseStatus.CREATED,
        attempt=1,
        created_at=now,
        expires_at=now + timedelta(minutes=20),
        updated_at=now,
    )


def test_sandbox_lease_is_scoped_and_version_fenced(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'leases.db'}")
    db.create_schema()
    repository = CodingSandboxLeaseRepository(db)
    acquisition = repository.acquire_attempt(_lease())
    created = acquisition.lease

    assert acquisition.created is True
    assert created.attempt == 1
    assert created.version == 1

    assert (
        repository.get_owned(lease_id="lease-1", workspace_id="analytics", owner_user_id="user-1")
        is not None
    )
    assert (
        repository.get_owned(lease_id="lease-1", workspace_id="foreign", owner_user_id="user-1")
        is None
    )
    assert (
        repository.transition(
            lease_id="lease-1",
            workspace_id="analytics",
            owner_user_id="user-1",
            expected_version=2,
            expected_statuses=("created",),
            status="running",
        )
        is None
    )
    updated = repository.transition(
        lease_id="lease-1",
        workspace_id="analytics",
        owner_user_id="user-1",
        expected_version=1,
        expected_statuses=("created",),
        status="checkpointed",
        checkpoint_ref="checkpoint-1",
    )
    assert updated is not None
    assert updated.version == 2
    assert updated.checkpoint_ref == "checkpoint-1"
    assert (
        repository.transition(
            lease_id="lease-1",
            workspace_id="analytics",
            owner_user_id="user-1",
            expected_version=1,
            expected_statuses=("created",),
            status="running",
        )
        is None
    )
    current = repository.get_owned(
        lease_id="lease-1",
        workspace_id="analytics",
        owner_user_id="user-1",
    )
    assert current is not None
    assert current.status == SandboxLeaseStatus.CHECKPOINTED
    assert current.version == 2
    assert (
        repository.transition(
            lease_id="lease-1",
            workspace_id="analytics",
            owner_user_id="user-1",
            expected_version=2,
            expected_statuses=("running",),
            status="closed",
        )
        is None
    )


def test_retry_allocates_next_attempt_after_failed_admission(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'retry.db'}")
    db.create_schema()
    repository = CodingSandboxLeaseRepository(db)
    first = repository.acquire_attempt(_lease()).lease
    failed = repository.transition(
        lease_id=first.id,
        workspace_id=first.workspace_id,
        owner_user_id=first.owner_user_id,
        expected_version=first.version,
        expected_statuses=("created",),
        status="failed",
    )
    assert failed is not None

    second = repository.acquire_attempt(_lease(lease_id="lease-2", job_name="coding-lease-2")).lease

    assert first.attempt == 1
    assert second.attempt == 2
    assert second.version == 1


def test_parallel_lifecycle_transition_has_single_winner(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'fencing.db'}")
    db.create_schema()
    created = CodingSandboxLeaseRepository(db).acquire_attempt(_lease()).lease

    def claim(status: str) -> SandboxTaskLease | None:
        return CodingSandboxLeaseRepository(db).transition(
            lease_id=created.id,
            workspace_id=created.workspace_id,
            owner_user_id=created.owner_user_id,
            expected_version=created.version,
            expected_statuses=("created",),
            status=status,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("running", "cancel_requested")))

    assert sum(item is not None for item in claims) == 1
    current = CodingSandboxLeaseRepository(db).get_owned(
        lease_id=created.id,
        workspace_id=created.workspace_id,
        owner_user_id=created.owner_user_id,
    )
    assert current is not None
    assert current.version == 2
    assert current.status in {
        SandboxLeaseStatus.RUNNING,
        SandboxLeaseStatus.CANCEL_REQUESTED,
    }


def test_parallel_retry_reuses_one_active_attempt_across_repository_instances(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'concurrent.db'}")
    db.create_schema()

    def create(index: int) -> tuple[str, int, bool]:
        repository = CodingSandboxLeaseRepository(db)
        acquisition = repository.acquire_attempt(
            _lease(
                lease_id=f"lease-{index}",
                job_name=f"coding-lease-{index}",
            )
        )
        return acquisition.lease.id, acquisition.lease.attempt, acquisition.created

    with ThreadPoolExecutor(max_workers=4) as executor:
        acquisitions = list(executor.map(create, range(1, 9)))

    assert {item[0] for item in acquisitions} == {"lease-1"}
    assert {item[1] for item in acquisitions} == {1}
    assert sum(item[2] for item in acquisitions) == 1


def test_active_attempt_retry_requires_same_owner_and_profile(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'conflict.db'}")
    db.create_schema()
    repository = CodingSandboxLeaseRepository(db)
    repository.acquire_attempt(_lease())
    conflicting = _lease(lease_id="lease-2", job_name="coding-lease-2").model_copy(
        update={"owner_user_id": "user-2"}
    )

    with pytest.raises(ValueError, match="active_sandbox_lease_conflict"):
        repository.acquire_attempt(conflicting)
