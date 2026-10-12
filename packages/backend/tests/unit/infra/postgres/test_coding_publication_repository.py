from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.coding import CodingReasonCode, PublicationReceipt
from rhetorica_backend.infra.postgres.coding_publication_repository import (
    CodingPublicationLedger,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _receipt(*, run_id: str = "run-1", state: str = "reserved") -> PublicationReceipt:
    now = datetime.now(tz=UTC)
    return PublicationReceipt(
        id=str(uuid4()),
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id=run_id,
        candidate_digest="c" * 64,
        validation_report_digest="d" * 64,
        state=state,  # type: ignore[arg-type]
        branch_name=f"feature/rhetorica/{run_id}",
        created_at=now,
        updated_at=now,
    )


def test_publication_ledger_is_unique_per_workspace_run_and_fenced(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'publication.db'}")
    db.create_schema()
    ledger = CodingPublicationLedger(db)
    first = ledger.reserve(_receipt())
    second = ledger.reserve(_receipt())
    assert first.id == second.id
    assert first.state == "reserved"
    assert first.attempt == 1

    foreign = ledger.get(workspace_id="other", run_id="run-1", owner_user_id="user-1")
    assert foreign is None
    owned = ledger.get(workspace_id="analytics", run_id="run-1", owner_user_id="user-1")
    assert owned is not None

    advanced = first.model_copy(
        update={
            "state": "commit_recorded",
            "commit_sha": "e" * 40,
            "updated_at": datetime.now(tz=UTC),
        }
    )
    saved = ledger.save(advanced, expected_state="reserved")
    assert saved is not None
    assert saved.attempt == 2
    assert ledger.save(advanced, expected_state="reserved") is None
    stored = ledger.get(workspace_id="analytics", run_id="run-1", owner_user_id="user-1")
    assert stored is not None
    assert stored.state == "commit_recorded"
    assert stored.commit_sha == "e" * 40
    assert stored.merge_request_url is None


def test_publication_ledger_rejects_stale_attempt_in_same_state(tmp_path) -> None:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'publication.db'}")
    db.create_schema()
    ledger = CodingPublicationLedger(db)
    reserved = ledger.reserve(_receipt())

    replaced = ledger.save(
        reserved.model_copy(update={"candidate_digest": "f" * 64}),
        expected_state="reserved",
    )
    assert replaced is not None
    assert replaced.attempt == 2

    stale = reserved.model_copy(update={"state": "commit_recorded", "commit_sha": "e" * 40})
    assert ledger.save(stale, expected_state="reserved") is None

    denied = ledger.save(
        replaced.model_copy(
            update={
                "reason_code": CodingReasonCode.BASE_CHANGED,
                "detail": "target_moved",
            }
        ),
        expected_state="reserved",
    )
    assert denied is not None
    assert denied.candidate_digest == "f" * 64
    assert denied.reason_code == CodingReasonCode.BASE_CHANGED
    assert denied.detail == "target_moved"
