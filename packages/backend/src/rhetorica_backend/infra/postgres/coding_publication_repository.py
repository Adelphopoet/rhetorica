from __future__ import annotations

from datetime import UTC

from rhetorica_backend.contracts.coding import PublicationReceipt
from rhetorica_backend.infra.postgres.coding_publication_models import (
    CodingPublicationOperationOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError


class CodingPublicationLedger:
    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    def get(
        self, *, workspace_id: str, run_id: str, owner_user_id: str
    ) -> PublicationReceipt | None:
        with self._db.session() as session:
            row = session.scalar(
                select(CodingPublicationOperationOrm).where(
                    CodingPublicationOperationOrm.workspace_id == workspace_id,
                    CodingPublicationOperationOrm.run_id == run_id,
                    CodingPublicationOperationOrm.owner_user_id == owner_user_id,
                )
            )
            return None if row is None else self._contract(row)

    def reserve(self, receipt: PublicationReceipt) -> PublicationReceipt:
        with self._db.session() as session:
            session.add(self._orm(receipt.model_copy(update={"attempt": 1})))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(CodingPublicationOperationOrm).where(
                        CodingPublicationOperationOrm.workspace_id == receipt.workspace_id,
                        CodingPublicationOperationOrm.run_id == receipt.run_id,
                    )
                )
                if existing is None or existing.owner_user_id != receipt.owner_user_id:
                    raise
                return self._contract(existing)
        loaded = self.get(
            workspace_id=receipt.workspace_id,
            run_id=receipt.run_id,
            owner_user_id=receipt.owner_user_id,
        )
        if loaded is None:
            raise RuntimeError("publication_ledger_reserve_failed")
        return loaded

    def save(
        self, receipt: PublicationReceipt, *, expected_state: str
    ) -> PublicationReceipt | None:
        table = CodingPublicationOperationOrm
        with self._db.session() as session:
            result = session.execute(
                update(table)
                .where(
                    table.id == receipt.id,
                    table.workspace_id == receipt.workspace_id,
                    table.owner_user_id == receipt.owner_user_id,
                    table.run_id == receipt.run_id,
                    table.state == expected_state,
                    table.attempt == receipt.attempt,
                )
                .values(
                    candidate_digest=receipt.candidate_digest,
                    validation_report_digest=receipt.validation_report_digest,
                    state=receipt.state,
                    branch_name=receipt.branch_name,
                    commit_sha=receipt.commit_sha,
                    merge_request_url=receipt.merge_request_url,
                    reason_code=receipt.reason_code,
                    detail=receipt.detail,
                    attempt=table.attempt + 1,
                    updated_at=receipt.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            session.commit()
            row = session.get(table, receipt.id, populate_existing=True)
            return None if row is None else self._contract(row)

    @staticmethod
    def _orm(receipt: PublicationReceipt) -> CodingPublicationOperationOrm:
        return CodingPublicationOperationOrm(
            id=receipt.id,
            workspace_id=receipt.workspace_id,
            owner_user_id=receipt.owner_user_id,
            run_id=receipt.run_id,
            candidate_digest=receipt.candidate_digest,
            validation_report_digest=receipt.validation_report_digest,
            state=receipt.state,
            branch_name=receipt.branch_name,
            commit_sha=receipt.commit_sha,
            merge_request_url=receipt.merge_request_url,
            reason_code=receipt.reason_code,
            detail=receipt.detail,
            attempt=receipt.attempt,
            created_at=receipt.created_at,
            updated_at=receipt.updated_at,
        )

    @staticmethod
    def _contract(row: CodingPublicationOperationOrm) -> PublicationReceipt:
        created = row.created_at
        updated = row.updated_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return PublicationReceipt(
            id=row.id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            run_id=row.run_id,
            candidate_digest=row.candidate_digest,
            validation_report_digest=row.validation_report_digest,
            state=row.state,  # type: ignore[arg-type]
            branch_name=row.branch_name,
            commit_sha=row.commit_sha,
            merge_request_url=row.merge_request_url,
            reason_code=row.reason_code,  # type: ignore[arg-type]
            detail=row.detail,
            attempt=int(row.attempt),
            created_at=created,
            updated_at=updated,
        )
