from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from rhetorica_backend.infra.postgres.models import (
    OAuthAuthorizationCodeOrm,
    OAuthAuthorizationTransactionOrm,
    OAuthClientOrm,
    OAuthTokenGrantOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.security.oauth import (
    IssuedOAuthSecret,
    OAuthClientMetadata,
    digest_oauth_secret,
    oauth_secret_prefix,
)
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _affected_rows(result: object) -> int:
    return int(cast(CursorResult[Any], result).rowcount or 0)


@dataclass(frozen=True)
class OAuthClientItem:
    client_id: str
    client_name: str
    client_uri: str | None
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    token_endpoint_auth_method: str
    client_secret_prefix: str | None
    client_secret_digest: str | None
    client_secret_expires_at: datetime | None
    registration_source_digest: str | None
    last_used_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class OAuthConsentItem:
    id: str
    client_id: str
    client_name: str
    client_uri: str | None
    redirect_uri: str
    scopes: tuple[str, ...]
    state: str | None
    code_challenge: str
    resource: str
    expires_at: datetime


@dataclass(frozen=True)
class OAuthCodeItem:
    id: str
    user_id: str
    workspace_id: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    code_challenge: str
    resource: str
    expires_at: datetime


@dataclass(frozen=True)
class OAuthTokenGrantItem:
    id: str
    family_id: str
    generation: int
    user_id: str
    workspace_id: str
    client_id: str
    scopes: tuple[str, ...]
    resource: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class OAuthTokenPair:
    access_token: str
    refresh_token: str
    grant: OAuthTokenGrantItem


@dataclass(frozen=True)
class OAuthCleanupResult:
    clients: int
    transactions: int
    codes: int
    grants: int


class OAuthRepository:
    def __init__(self, db: DatabaseRuntime, *, pepper: str):
        if not pepper:
            raise ValueError("oauth_token_pepper_required")
        self._db = db
        self._pepper = pepper

    def register_client(
        self,
        *,
        metadata: OAuthClientMetadata,
        client_id: str,
        client_secret: IssuedOAuthSecret | None,
        registration_source_digest: str | None = None,
        secret_lifetime: timedelta | None = None,
        now: datetime | None = None,
    ) -> OAuthClientItem:
        issued_at = now or datetime.now(tz=UTC)
        row = OAuthClientOrm(
            client_id=client_id,
            client_name=metadata.client_name,
            client_uri=metadata.client_uri,
            redirect_uris=list(metadata.redirect_uris),
            grant_types=list(metadata.grant_types),
            response_types=list(metadata.response_types),
            token_endpoint_auth_method=metadata.token_endpoint_auth_method,
            client_secret_prefix=client_secret.prefix if client_secret else None,
            client_secret_digest=client_secret.digest if client_secret else None,
            client_secret_expires_at=issued_at + secret_lifetime
            if client_secret and secret_lifetime
            else None,
            registration_source_digest=registration_source_digest,
            last_used_at=None,
            created_at=issued_at,
            updated_at=issued_at,
        )
        with self._db.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._client_item(row)

    def count_clients(self) -> int:
        with self._db.session() as session:
            return int(
                session.execute(select(func.count()).select_from(OAuthClientOrm)).scalar_one()
            )

    def count_recent_registrations(
        self,
        *,
        registration_source_digest: str,
        created_since: datetime,
    ) -> int:
        with self._db.session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(OAuthClientOrm)
                    .where(
                        OAuthClientOrm.registration_source_digest == registration_source_digest,
                        OAuthClientOrm.created_at >= created_since,
                    )
                ).scalar_one()
            )

    def touch_client(self, *, client_id: str, now: datetime | None = None) -> bool:
        used_at = now or datetime.now(tz=UTC)
        with self._db.session() as session:
            result = session.execute(
                update(OAuthClientOrm)
                .where(OAuthClientOrm.client_id == client_id)
                .values(last_used_at=used_at, updated_at=used_at)
            )
            session.commit()
            return bool(_affected_rows(result))

    def cleanup_expired(
        self,
        *,
        invalid_before: datetime,
        inactive_client_before: datetime,
    ) -> OAuthCleanupResult:
        """Delete only unusable OAuth records while preserving active replay history."""
        with self._db.session() as session:
            transactions = _affected_rows(
                session.execute(
                    delete(OAuthAuthorizationTransactionOrm).where(
                        OAuthAuthorizationTransactionOrm.expires_at <= invalid_before
                    )
                )
            )
            codes = _affected_rows(
                session.execute(
                    delete(OAuthAuthorizationCodeOrm).where(
                        OAuthAuthorizationCodeOrm.expires_at <= invalid_before
                    )
                )
            )
            grants = _affected_rows(
                session.execute(
                    delete(OAuthTokenGrantOrm).where(
                        or_(
                            OAuthTokenGrantOrm.refresh_expires_at <= invalid_before,
                            and_(
                                OAuthTokenGrantOrm.revoked_at.is_not(None),
                                OAuthTokenGrantOrm.revoked_at <= invalid_before,
                            ),
                        )
                    )
                )
            )
            has_transaction = exists().where(
                OAuthAuthorizationTransactionOrm.client_id == OAuthClientOrm.client_id
            )
            has_code = exists().where(
                OAuthAuthorizationCodeOrm.client_id == OAuthClientOrm.client_id
            )
            has_grant = exists().where(OAuthTokenGrantOrm.client_id == OAuthClientOrm.client_id)
            clients = _affected_rows(
                session.execute(
                    delete(OAuthClientOrm).where(
                        func.coalesce(OAuthClientOrm.last_used_at, OAuthClientOrm.created_at)
                        <= inactive_client_before,
                        ~has_transaction,
                        ~has_code,
                        ~has_grant,
                    )
                )
            )
            session.commit()
            return OAuthCleanupResult(
                clients=clients,
                transactions=transactions,
                codes=codes,
                grants=grants,
            )

    def get_client(self, *, client_id: str) -> OAuthClientItem | None:
        with self._db.session() as session:
            row = session.get(OAuthClientOrm, client_id)
            return self._client_item(row) if row else None

    def authenticate_client_secret(
        self,
        *,
        client_id: str,
        client_secret: str,
        now: datetime | None = None,
    ) -> OAuthClientItem | None:
        checked_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(client_secret, kind="client_secret")
        if prefix is None:
            return None
        with self._db.session() as session:
            row = session.get(OAuthClientOrm, client_id)
            if (
                row is None
                or row.client_secret_prefix != prefix
                or row.client_secret_digest is None
                or not secrets.compare_digest(
                    row.client_secret_digest,
                    digest_oauth_secret(client_secret, pepper=self._pepper),
                )
                or (
                    row.client_secret_expires_at is not None
                    and _utc(row.client_secret_expires_at) <= checked_at
                )
            ):
                return None
            return self._client_item(row)

    def create_consent(
        self,
        *,
        transaction: IssuedOAuthSecret,
        client: OAuthClientMetadata,
        redirect_uri: str,
        scopes: tuple[str, ...],
        state: str | None,
        code_challenge: str,
        resource: str,
        lifetime: timedelta,
        now: datetime | None = None,
    ) -> OAuthConsentItem:
        created_at = now or datetime.now(tz=UTC)
        row = OAuthAuthorizationTransactionOrm(
            transaction_prefix=transaction.prefix,
            transaction_digest=transaction.digest,
            client_id=client.client_id,
            client_name=client.client_name,
            client_uri=client.client_uri,
            redirect_uri=redirect_uri,
            scopes=list(scopes),
            state=state,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=created_at + lifetime,
            consumed_at=None,
            created_at=created_at,
        )
        with self._db.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._consent_item(row)

    def get_active_consent(
        self, *, transaction_token: str, now: datetime | None = None
    ) -> OAuthConsentItem | None:
        checked_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(transaction_token, kind="transaction")
        if prefix is None:
            return None
        digest = digest_oauth_secret(transaction_token, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthAuthorizationTransactionOrm).where(
                    OAuthAuthorizationTransactionOrm.transaction_prefix == prefix
                )
            ).scalar_one_or_none()
            if (
                row is None
                or not secrets.compare_digest(row.transaction_digest, digest)
                or row.consumed_at is not None
                or _utc(row.expires_at) <= checked_at
            ):
                return None
            return self._consent_item(row)

    def approve_consent(
        self,
        *,
        transaction_token: str,
        code: IssuedOAuthSecret,
        user_id: str,
        workspace_id: str,
        code_lifetime: timedelta,
        now: datetime | None = None,
    ) -> tuple[OAuthConsentItem, OAuthCodeItem] | None:
        approved_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(transaction_token, kind="transaction")
        if prefix is None:
            return None
        digest = digest_oauth_secret(transaction_token, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthAuthorizationTransactionOrm)
                .where(OAuthAuthorizationTransactionOrm.transaction_prefix == prefix)
                .with_for_update()
            ).scalar_one_or_none()
            if (
                row is None
                or not secrets.compare_digest(row.transaction_digest, digest)
                or row.consumed_at is not None
                or _utc(row.expires_at) <= approved_at
            ):
                return None
            row.consumed_at = approved_at
            code_row = OAuthAuthorizationCodeOrm(
                code_prefix=code.prefix,
                code_digest=code.digest,
                user_id=user_id,
                workspace_id=workspace_id,
                client_id=row.client_id,
                redirect_uri=row.redirect_uri,
                scopes=list(row.scopes or []),
                code_challenge=row.code_challenge,
                resource=row.resource,
                expires_at=approved_at + code_lifetime,
                consumed_at=None,
                created_at=approved_at,
            )
            session.add(code_row)
            session.commit()
            session.refresh(code_row)
            return self._consent_item(row), self._code_item(code_row)

    def deny_consent(
        self, *, transaction_token: str, now: datetime | None = None
    ) -> OAuthConsentItem | None:
        denied_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(transaction_token, kind="transaction")
        if prefix is None:
            return None
        digest = digest_oauth_secret(transaction_token, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthAuthorizationTransactionOrm)
                .where(OAuthAuthorizationTransactionOrm.transaction_prefix == prefix)
                .with_for_update()
            ).scalar_one_or_none()
            if (
                row is None
                or not secrets.compare_digest(row.transaction_digest, digest)
                or row.consumed_at is not None
                or _utc(row.expires_at) <= denied_at
            ):
                return None
            row.consumed_at = denied_at
            session.commit()
            return self._consent_item(row)

    def consume_code(
        self,
        *,
        code_token: str,
        client_id: str,
        redirect_uri: str,
        resource: str,
        code_challenge: str,
        now: datetime | None = None,
    ) -> OAuthCodeItem | None:
        consumed_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(code_token, kind="code")
        if prefix is None:
            return None
        digest = digest_oauth_secret(code_token, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthAuthorizationCodeOrm)
                .where(OAuthAuthorizationCodeOrm.code_prefix == prefix)
                .with_for_update()
            ).scalar_one_or_none()
            if (
                row is None
                or not secrets.compare_digest(row.code_digest, digest)
                or row.consumed_at is not None
                or _utc(row.expires_at) <= consumed_at
                or row.client_id != client_id
                or row.redirect_uri != redirect_uri
                or row.resource != resource
                or not secrets.compare_digest(row.code_challenge, code_challenge)
            ):
                return None
            row.consumed_at = consumed_at
            session.commit()
            return self._code_item(row)

    def issue_token_pair(
        self,
        *,
        code: OAuthCodeItem,
        access_token: IssuedOAuthSecret,
        refresh_token: IssuedOAuthSecret,
        access_lifetime: timedelta,
        refresh_lifetime: timedelta,
        now: datetime | None = None,
    ) -> OAuthTokenPair:
        issued_at = now or datetime.now(tz=UTC)
        return self._insert_token_pair(
            family_id=str(uuid4()),
            generation=0,
            user_id=code.user_id,
            workspace_id=code.workspace_id,
            client_id=code.client_id,
            scopes=code.scopes,
            resource=code.resource,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=issued_at + access_lifetime,
            refresh_expires_at=issued_at + refresh_lifetime,
            now=issued_at,
        )

    def find_access_token(
        self, *, access_token: str, resource: str, now: datetime | None = None
    ) -> OAuthTokenGrantItem | None:
        checked_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(access_token, kind="access")
        if prefix is None:
            return None
        digest = digest_oauth_secret(access_token, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthTokenGrantOrm).where(OAuthTokenGrantOrm.access_token_prefix == prefix)
            ).scalar_one_or_none()
            if (
                row is None
                or not secrets.compare_digest(row.access_token_digest, digest)
                or row.revoked_at is not None
                or row.rotated_at is not None
                or _utc(row.access_expires_at) <= checked_at
                or row.resource != resource
            ):
                return None
            return self._grant_item(row)

    def rotate_refresh_token(
        self,
        *,
        refresh_token_value: str,
        client_id: str,
        resource: str,
        requested_scopes: tuple[str, ...] | None,
        access_token: IssuedOAuthSecret,
        refresh_token: IssuedOAuthSecret,
        access_lifetime: timedelta,
        now: datetime | None = None,
    ) -> tuple[str, OAuthTokenPair | None]:
        rotated_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(refresh_token_value, kind="refresh")
        if prefix is None:
            return "invalid", None
        digest = digest_oauth_secret(refresh_token_value, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthTokenGrantOrm)
                .where(OAuthTokenGrantOrm.refresh_token_prefix == prefix)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None or not secrets.compare_digest(row.refresh_token_digest, digest):
                return "invalid", None
            if row.rotated_at is not None:
                session.execute(
                    update(OAuthTokenGrantOrm)
                    .where(OAuthTokenGrantOrm.family_id == row.family_id)
                    .values(revoked_at=rotated_at, updated_at=rotated_at)
                )
                session.commit()
                return "replayed", None
            if (
                row.revoked_at is not None
                or _utc(row.refresh_expires_at) <= rotated_at
                or row.client_id != client_id
                or row.resource != resource
            ):
                return "invalid", None
            scopes = tuple(row.scopes or [])
            if requested_scopes is not None:
                if not set(requested_scopes).issubset(scopes):
                    return "invalid_scope", None
                scopes = requested_scopes
            row.rotated_at = rotated_at
            row.updated_at = rotated_at
            new_row = OAuthTokenGrantOrm(
                family_id=row.family_id,
                generation=row.generation + 1,
                user_id=row.user_id,
                workspace_id=row.workspace_id,
                client_id=row.client_id,
                scopes=list(scopes),
                resource=row.resource,
                access_token_prefix=access_token.prefix,
                access_token_digest=access_token.digest,
                refresh_token_prefix=refresh_token.prefix,
                refresh_token_digest=refresh_token.digest,
                access_expires_at=rotated_at + access_lifetime,
                refresh_expires_at=_utc(row.refresh_expires_at),
                rotated_at=None,
                revoked_at=None,
                created_at=rotated_at,
                updated_at=rotated_at,
            )
            session.add(new_row)
            session.commit()
            session.refresh(new_row)
            return "ok", OAuthTokenPair(
                access_token=access_token.value,
                refresh_token=refresh_token.value,
                grant=self._grant_item(new_row),
            )

    def revoke_token_family(
        self, *, token_value: str, client_id: str, now: datetime | None = None
    ) -> bool:
        revoked_at = now or datetime.now(tz=UTC)
        prefix = oauth_secret_prefix(token_value)
        if prefix is None:
            return False
        digest = digest_oauth_secret(token_value, pepper=self._pepper)
        with self._db.session() as session:
            row = session.execute(
                select(OAuthTokenGrantOrm).where(
                    or_(
                        OAuthTokenGrantOrm.access_token_prefix == prefix,
                        OAuthTokenGrantOrm.refresh_token_prefix == prefix,
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.client_id != client_id:
                return False
            expected = (
                row.access_token_digest
                if row.access_token_prefix == prefix
                else row.refresh_token_digest
            )
            if not secrets.compare_digest(expected, digest):
                return False
            session.execute(
                update(OAuthTokenGrantOrm)
                .where(OAuthTokenGrantOrm.family_id == row.family_id)
                .values(revoked_at=revoked_at, updated_at=revoked_at)
            )
            session.commit()
            return True

    def _insert_token_pair(
        self,
        *,
        family_id: str,
        generation: int,
        user_id: str,
        workspace_id: str,
        client_id: str,
        scopes: tuple[str, ...],
        resource: str,
        access_token: IssuedOAuthSecret,
        refresh_token: IssuedOAuthSecret,
        access_expires_at: datetime,
        refresh_expires_at: datetime,
        now: datetime,
    ) -> OAuthTokenPair:
        row = OAuthTokenGrantOrm(
            family_id=family_id,
            generation=generation,
            user_id=user_id,
            workspace_id=workspace_id,
            client_id=client_id,
            scopes=list(scopes),
            resource=resource,
            access_token_prefix=access_token.prefix,
            access_token_digest=access_token.digest,
            refresh_token_prefix=refresh_token.prefix,
            refresh_token_digest=refresh_token.digest,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            rotated_at=None,
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._db.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return OAuthTokenPair(
                access_token=access_token.value,
                refresh_token=refresh_token.value,
                grant=self._grant_item(row),
            )

    @staticmethod
    def _client_item(row: OAuthClientOrm) -> OAuthClientItem:
        return OAuthClientItem(
            client_id=row.client_id,
            client_name=row.client_name,
            client_uri=row.client_uri,
            redirect_uris=tuple(row.redirect_uris or []),
            grant_types=tuple(row.grant_types or []),
            response_types=tuple(row.response_types or []),
            token_endpoint_auth_method=row.token_endpoint_auth_method,
            client_secret_prefix=row.client_secret_prefix,
            client_secret_digest=row.client_secret_digest,
            client_secret_expires_at=_utc(row.client_secret_expires_at)
            if row.client_secret_expires_at
            else None,
            registration_source_digest=row.registration_source_digest,
            last_used_at=_utc(row.last_used_at) if row.last_used_at else None,
            created_at=_utc(row.created_at),
        )

    @staticmethod
    def _consent_item(row: OAuthAuthorizationTransactionOrm) -> OAuthConsentItem:
        return OAuthConsentItem(
            id=row.id,
            client_id=row.client_id,
            client_name=row.client_name,
            client_uri=row.client_uri,
            redirect_uri=row.redirect_uri,
            scopes=tuple(row.scopes or []),
            state=row.state,
            code_challenge=row.code_challenge,
            resource=row.resource,
            expires_at=_utc(row.expires_at),
        )

    @staticmethod
    def _code_item(row: OAuthAuthorizationCodeOrm) -> OAuthCodeItem:
        return OAuthCodeItem(
            id=row.id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            client_id=row.client_id,
            redirect_uri=row.redirect_uri,
            scopes=tuple(row.scopes or []),
            code_challenge=row.code_challenge,
            resource=row.resource,
            expires_at=_utc(row.expires_at),
        )

    @staticmethod
    def _grant_item(row: OAuthTokenGrantOrm) -> OAuthTokenGrantItem:
        return OAuthTokenGrantItem(
            id=row.id,
            family_id=row.family_id,
            generation=row.generation,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            client_id=row.client_id,
            scopes=tuple(row.scopes or []),
            resource=row.resource,
            access_expires_at=_utc(row.access_expires_at),
            refresh_expires_at=_utc(row.refresh_expires_at),
            rotated_at=_utc(row.rotated_at) if row.rotated_at else None,
            revoked_at=_utc(row.revoked_at) if row.revoked_at else None,
            created_at=_utc(row.created_at),
        )
