from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rhetorica_backend.infra.postgres.models import PersonalAccessTokenOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class PersonalAccessTokenItem:
    id: str
    user_id: str
    workspace_id: str
    name: str
    token_prefix: str
    token_digest: str
    scopes: tuple[str, ...]
    lifetime_days: int
    auto_renew: bool
    expires_at: datetime
    last_used_at: datetime | None
    last_renewed_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(tz=UTC)


class PersonalAccessTokenRepository:
    """Persistence gateway for revocable hash-only user credentials."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db

    def create(
        self,
        *,
        user_id: str,
        workspace_id: str,
        name: str,
        token_prefix: str,
        token_digest: str,
        scopes: tuple[str, ...],
        lifetime_days: int,
        auto_renew: bool,
        now: datetime | None = None,
    ) -> PersonalAccessTokenItem:
        issued_at = now or datetime.now(tz=UTC)
        row = PersonalAccessTokenOrm(
            user_id=user_id,
            workspace_id=workspace_id,
            name=name.strip(),
            token_prefix=token_prefix,
            token_digest=token_digest,
            scopes=list(scopes),
            lifetime_days=lifetime_days,
            auto_renew=auto_renew,
            expires_at=issued_at + timedelta(days=lifetime_days),
            last_used_at=None,
            last_renewed_at=None,
            revoked_at=None,
            created_at=issued_at,
            updated_at=issued_at,
        )
        with self._db.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_item(row)

    def list_for_user(self, *, user_id: str) -> list[PersonalAccessTokenItem]:
        with self._db.session() as session:
            rows = session.execute(
                select(PersonalAccessTokenOrm)
                .where(PersonalAccessTokenOrm.user_id == user_id)
                .order_by(PersonalAccessTokenOrm.created_at.desc())
            ).scalars()
            return [self._to_item(row) for row in rows]

    def find_for_authentication(
        self,
        *,
        token_prefix: str,
        token_digest: str,
        now: datetime | None = None,
    ) -> PersonalAccessTokenItem | None:
        checked_at = now or datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(PersonalAccessTokenOrm).where(
                    PersonalAccessTokenOrm.token_prefix == token_prefix
                )
            ).scalar_one_or_none()
            if row is None or not secrets.compare_digest(row.token_digest, token_digest):
                return None
            if row.revoked_at is not None or _utc(row.expires_at) <= checked_at:
                return None
            return self._to_item(row)

    def record_successful_use(
        self,
        *,
        token_id: str,
        now: datetime | None = None,
    ) -> PersonalAccessTokenItem | None:
        used_at = now or datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(PersonalAccessTokenOrm, token_id)
            if row is None or row.revoked_at is not None or _utc(row.expires_at) <= used_at:
                return None
            renewal_due = row.auto_renew and (
                row.last_renewed_at is None
                or _utc(row.last_renewed_at) <= used_at - timedelta(days=1)
            )
            usage_update_due = row.last_used_at is None or _utc(row.last_used_at) <= used_at - timedelta(
                minutes=5
            )
            if not renewal_due and not usage_update_due:
                return self._to_item(row)
            row.last_used_at = used_at
            if renewal_due:
                row.expires_at = used_at + timedelta(days=row.lifetime_days)
                row.last_renewed_at = used_at
            row.updated_at = used_at
            session.commit()
            session.refresh(row)
            return self._to_item(row)

    def revoke(self, *, token_id: str, user_id: str) -> bool:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(PersonalAccessTokenOrm, token_id)
            if row is None or row.user_id != user_id:
                return False
            if row.revoked_at is None:
                row.revoked_at = now
                row.updated_at = now
                session.commit()
            return True

    @staticmethod
    def _to_item(row: PersonalAccessTokenOrm) -> PersonalAccessTokenItem:
        return PersonalAccessTokenItem(
            id=row.id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            name=row.name,
            token_prefix=row.token_prefix,
            token_digest=row.token_digest,
            scopes=tuple(row.scopes or []),
            lifetime_days=row.lifetime_days,
            auto_renew=row.auto_renew,
            expires_at=_utc(row.expires_at),
            last_used_at=_utc(row.last_used_at) if row.last_used_at else None,
            last_renewed_at=_utc(row.last_renewed_at) if row.last_renewed_at else None,
            revoked_at=_utc(row.revoked_at) if row.revoked_at else None,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )
