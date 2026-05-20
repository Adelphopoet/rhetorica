from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.infra.postgres.models import UserAccountOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

LOCAL_AUTH_PROVIDER = "local"
BOOTSTRAP_AUTH_PROVIDER = "bootstrap"
BOOTSTRAP_AUTH_SUBJECT = "bootstrap-admin"
BOOTSTRAP_DISPLAY_NAME = "Bootstrap Admin"
ADMIN_ROLE = "admin"
ANALYST_ROLE = "analyst"
SUPPORTED_LOCAL_USER_ROLES = (ADMIN_ROLE, ANALYST_ROLE)


class UserAccountItem:
    """Domain representation of a control-plane user."""

    def __init__(
        self,
        user_id: str,
        workspace_id: str,
        login: str,
        password_hash: str | None,
        role: str,
        is_active: bool,
        auth_provider: str,
        auth_subject: str,
        email: str | None,
        display_name: str | None,
        is_bootstrap_user: bool,
        created_at: datetime,
        updated_at: datetime,
        last_seen_at: datetime | None,
    ) -> None:
        self.id = user_id
        self.workspace_id = workspace_id
        self.login = login
        self.password_hash = password_hash
        self.role = role
        self.is_active = is_active
        self.auth_provider = auth_provider
        self.auth_subject = auth_subject
        self.email = email
        self.display_name = display_name
        self.is_bootstrap_user = is_bootstrap_user
        self.created_at = created_at
        self.updated_at = updated_at
        self.last_seen_at = last_seen_at

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE


class UserAccountRepository:
    """Persistence gateway for user accounts and bootstrap identity provisioning."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db

    @staticmethod
    def normalize_login(login: str) -> str:
        normalized = login.strip().lower()
        if not normalized:
            raise ValueError("login_required")
        return normalized

    def ensure_bootstrap_user(self, workspace_id: str) -> UserAccountItem:
        """Get or create the bootstrap admin user for a workspace."""
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(UserAccountOrm).where(
                    UserAccountOrm.workspace_id == workspace_id,
                    UserAccountOrm.auth_provider == BOOTSTRAP_AUTH_PROVIDER,
                    UserAccountOrm.auth_subject == BOOTSTRAP_AUTH_SUBJECT,
                )
            ).scalar_one_or_none()

            if row is None:
                row = UserAccountOrm(
                    workspace_id=workspace_id,
                    login="bootstrap-admin",
                    password_hash=None,
                    role=ADMIN_ROLE,
                    is_active=True,
                    auth_provider=BOOTSTRAP_AUTH_PROVIDER,
                    auth_subject=BOOTSTRAP_AUTH_SUBJECT,
                    email=None,
                    display_name=BOOTSTRAP_DISPLAY_NAME,
                    is_bootstrap_user=True,
                    created_at=now,
                    updated_at=now,
                    last_seen_at=now,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    row = session.execute(
                        select(UserAccountOrm).where(
                            UserAccountOrm.workspace_id == workspace_id,
                            UserAccountOrm.auth_provider == BOOTSTRAP_AUTH_PROVIDER,
                            UserAccountOrm.auth_subject == BOOTSTRAP_AUTH_SUBJECT,
                        )
                    ).scalar_one()
                else:
                    session.refresh(row)

            row.login = "bootstrap-admin"
            row.role = ADMIN_ROLE
            row.is_active = True
            row.auth_provider = BOOTSTRAP_AUTH_PROVIDER
            row.auth_subject = BOOTSTRAP_AUTH_SUBJECT
            row.is_bootstrap_user = True
            row.display_name = BOOTSTRAP_DISPLAY_NAME
            row.last_seen_at = now
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def get_by_auth_identity(
        self,
        *,
        workspace_id: str,
        auth_provider: str,
        auth_subject: str,
    ) -> UserAccountItem | None:
        with self._db.session() as session:
            row = session.execute(
                select(UserAccountOrm).where(
                    UserAccountOrm.workspace_id == workspace_id,
                    UserAccountOrm.auth_provider == auth_provider,
                    UserAccountOrm.auth_subject == auth_subject,
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row is not None else None

    def get_by_login(self, *, workspace_id: str, login: str) -> UserAccountItem | None:
        normalized_login = self.normalize_login(login)
        with self._db.session() as session:
            row = session.execute(
                select(UserAccountOrm).where(
                    UserAccountOrm.workspace_id == workspace_id,
                    UserAccountOrm.login == normalized_login,
                )
            ).scalar_one_or_none()
            return self._to_domain(row) if row is not None else None

    def list_by_workspace(self, *, workspace_id: str) -> list[UserAccountItem]:
        with self._db.session() as session:
            rows = session.execute(
                select(UserAccountOrm)
                .where(UserAccountOrm.workspace_id == workspace_id)
                .order_by(UserAccountOrm.created_at.asc(), UserAccountOrm.id.asc())
            ).scalars()
            return [self._to_domain(row) for row in rows]

    def create_local_user(
        self,
        *,
        workspace_id: str,
        login: str,
        password_hash: str,
        role: str = ADMIN_ROLE,
        display_name: str | None = None,
        email: str | None = None,
        is_active: bool = True,
    ) -> UserAccountItem:
        normalized_login = self.normalize_login(login)
        now = datetime.now(tz=UTC)
        row = UserAccountOrm(
            workspace_id=workspace_id,
            login=normalized_login,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            auth_provider=LOCAL_AUTH_PROVIDER,
            auth_subject=f"local-user:{uuid4()}",
            email=email,
            display_name=display_name,
            is_bootstrap_user=False,
            created_at=now,
            updated_at=now,
            last_seen_at=None,
        )
        with self._db.session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def delete_local_user(self, *, workspace_id: str, user_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(UserAccountOrm, user_id)
            if row is None:
                return False
            if row.workspace_id != workspace_id:
                return False
            if row.auth_provider != LOCAL_AUTH_PROVIDER:
                return False
            session.delete(row)
            session.commit()
            return True

    def ensure_local_admin(
        self,
        *,
        workspace_id: str,
        login: str,
        password_hash: str,
        display_name: str | None = None,
    ) -> tuple[UserAccountItem, bool]:
        normalized_login = self.normalize_login(login)
        existing = self.get_by_login(workspace_id=workspace_id, login=normalized_login)
        if existing is not None:
            if existing.auth_provider != LOCAL_AUTH_PROVIDER:
                raise ValueError("admin_seed_login_belongs_to_non_local_account")
            if existing.role != ADMIN_ROLE:
                raise ValueError("admin_seed_login_exists_without_admin_role")
            return existing, False
        created = self.create_local_user(
            workspace_id=workspace_id,
            login=normalized_login,
            password_hash=password_hash,
            role=ADMIN_ROLE,
            display_name=display_name or normalized_login,
            is_active=True,
        )
        return created, True

    def update_password_hash(self, *, user_id: str, password_hash: str) -> UserAccountItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(UserAccountOrm, user_id)
            if row is None:
                return None
            row.password_hash = password_hash
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def touch_last_seen(self, *, user_id: str) -> UserAccountItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(UserAccountOrm, user_id)
            if row is None:
                return None
            row.last_seen_at = now
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: UserAccountOrm) -> UserAccountItem:
        return UserAccountItem(
            user_id=row.id,
            workspace_id=row.workspace_id,
            login=row.login,
            password_hash=row.password_hash,
            role=row.role,
            is_active=row.is_active,
            auth_provider=row.auth_provider,
            auth_subject=row.auth_subject,
            email=row.email,
            display_name=row.display_name,
            is_bootstrap_user=row.is_bootstrap_user,
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_seen_at=row.last_seen_at,
        )
