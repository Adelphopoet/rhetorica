"""Persistence and deterministic resolution for workspace-scoped resources."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from rhetorica_backend.contracts.workspaces import (
    CreateWorkspaceRequest,
    CreateWorkspaceResourceGrantRequest,
    ResolvedWorkspaceResource,
    UpsertWorkspaceMembershipRequest,
    WorkspaceItem,
    WorkspaceMembershipItem,
    WorkspacePolicyAuditEventItem,
    WorkspaceResourceCatalogItem,
    WorkspaceResourceGrantItem,
    WorkspaceResourceKind,
    WorkspaceRole,
)
from rhetorica_backend.infra.postgres.coding_policy_models import DefaultAssistantPolicyOrm
from rhetorica_backend.infra.postgres.models import (
    AirflowConnectorConfigOrm,
    ConnectorConfigOrm,
    DocumentationConnectorConfigOrm,
    GitLabConnectorConfigOrm,
    KubernetesConnectorConfigOrm,
    LlmConnectorConfigOrm,
    MattermostConnectorConfigOrm,
    McpConnectorConfigOrm,
    RagConnectorConfigOrm,
    UserAccountOrm,
    VictoriaMetricsConnectorConfigOrm,
    WorkspaceSkillOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_models import (
    WorkspaceMembershipOrm,
    WorkspaceOrm,
    WorkspacePolicyAuditEventOrm,
    WorkspaceResourceGrantOrm,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

RESOURCE_MODELS: dict[str, type[Any]] = {
    WorkspaceResourceKind.DATABASE_CONNECTOR.value: ConnectorConfigOrm,
    WorkspaceResourceKind.LLM_CONNECTOR.value: LlmConnectorConfigOrm,
    WorkspaceResourceKind.RAG_CONNECTOR.value: RagConnectorConfigOrm,
    WorkspaceResourceKind.DOCUMENTATION_CONNECTOR.value: DocumentationConnectorConfigOrm,
    WorkspaceResourceKind.GITLAB_CONNECTOR.value: GitLabConnectorConfigOrm,
    WorkspaceResourceKind.MCP_CONNECTOR.value: McpConnectorConfigOrm,
    WorkspaceResourceKind.AIRFLOW_CONNECTOR.value: AirflowConnectorConfigOrm,
    WorkspaceResourceKind.KUBERNETES_CONNECTOR.value: KubernetesConnectorConfigOrm,
    WorkspaceResourceKind.VICTORIAMETRICS_CONNECTOR.value: VictoriaMetricsConnectorConfigOrm,
    WorkspaceResourceKind.MATTERMOST_CONNECTOR.value: MattermostConnectorConfigOrm,
    WorkspaceResourceKind.WORKSPACE_SKILL.value: WorkspaceSkillOrm,
}


def _active_predicate(model: type[Any], kind: str) -> Any:
    if kind == WorkspaceResourceKind.WORKSPACE_SKILL.value:
        return model.status == "active"
    return model.is_active.is_(True)


def _resource_is_active(resource: Any, kind: str) -> bool:
    if kind == WorkspaceResourceKind.WORKSPACE_SKILL.value:
        return resource.status == "active"
    return bool(resource.is_active)


class WorkspaceConflictError(ValueError):
    pass


class WorkspaceRepository:
    def __init__(self, db: DatabaseRuntime, *, default_workspace_id: str = "default"):
        self._db = db
        self._default_workspace_id = default_workspace_id

    def ensure_home_membership(self, *, user_id: str) -> WorkspaceMembershipItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            user = session.get(UserAccountOrm, user_id)
            if user is None:
                raise LookupError("user_not_found")
            workspace = session.get(WorkspaceOrm, user.workspace_id)
            if workspace is None:
                workspace = WorkspaceOrm(
                    id=user.workspace_id,
                    name=user.workspace_id,
                    is_active=True,
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
                session.add(workspace)
                session.flush()
                self._ensure_default_assistant_policy(
                    session, workspace_id=workspace.id, actor_user_id=user.id, now=now
                )
            elif user.workspace_id == self._default_workspace_id and not workspace.is_active:
                workspace.is_active = True
                workspace.updated_at = now
            membership = session.get(WorkspaceMembershipOrm, (user.workspace_id, user.id))
            if membership is None:
                membership = WorkspaceMembershipOrm(
                    workspace_id=user.workspace_id,
                    user_id=user.id,
                    role=(
                        WorkspaceRole.OWNER.value
                        if user.role == "admin"
                        else WorkspaceRole.ANALYST.value
                    ),
                    is_active=user.is_active,
                    created_at=now,
                    updated_at=now,
                )
                session.add(membership)
            elif user.workspace_id == self._default_workspace_id and (
                membership.role == WorkspaceRole.VIEWER.value or not membership.is_active
            ):
                membership.role = WorkspaceRole.ANALYST.value
                membership.is_active = True
                membership.updated_at = now
            if user.workspace_id != self._default_workspace_id:
                default_workspace = session.get(WorkspaceOrm, self._default_workspace_id)
                if default_workspace is None:
                    default_workspace = WorkspaceOrm(
                        id=self._default_workspace_id,
                        name="Default",
                        is_active=True,
                        metadata_json={},
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(default_workspace)
                    session.flush()
                    self._ensure_default_assistant_policy(
                        session,
                        workspace_id=default_workspace.id,
                        actor_user_id=user.id,
                        now=now,
                    )
                elif not default_workspace.is_active:
                    default_workspace.is_active = True
                    default_workspace.updated_at = now
                default_membership = session.get(
                    WorkspaceMembershipOrm,
                    (self._default_workspace_id, user.id),
                )
                if default_membership is None:
                    session.add(
                        WorkspaceMembershipOrm(
                            workspace_id=self._default_workspace_id,
                            user_id=user.id,
                            role=WorkspaceRole.ANALYST.value,
                            is_active=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                elif (
                    default_membership.role == WorkspaceRole.VIEWER.value
                    or not default_membership.is_active
                ):
                    default_membership.role = WorkspaceRole.ANALYST.value
                    default_membership.is_active = True
                    default_membership.updated_at = now
            session.commit()
            session.refresh(membership)
            return self._membership(membership, user=user)

    def create(self, request: CreateWorkspaceRequest, *, actor_user_id: str) -> WorkspaceItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = WorkspaceOrm(
                id=request.id,
                name=request.name.strip(),
                is_active=True,
                metadata_json=dict(request.metadata),
                created_at=now,
                updated_at=now,
            )
            try:
                session.add(row)
                session.flush()
                self._ensure_default_assistant_policy(
                    session,
                    workspace_id=request.id,
                    actor_user_id=actor_user_id,
                    now=now,
                )
                session.add(
                    WorkspaceMembershipOrm(
                        workspace_id=request.id,
                        user_id=actor_user_id,
                        role=WorkspaceRole.OWNER.value,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
                self._audit(
                    session,
                    workspace_id=request.id,
                    actor_user_id=actor_user_id,
                    target_type="workspace",
                    target_id=request.id,
                    action="created",
                    current_value={"name": request.name, "metadata": request.metadata},
                    now=now,
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise WorkspaceConflictError("workspace_conflict") from exc
            session.refresh(row)
            return self._workspace(row)

    def list_for_user(self, *, user_id: str) -> list[tuple[WorkspaceItem, WorkspaceMembershipItem]]:
        self.ensure_home_membership(user_id=user_id)
        with self._db.session() as session:
            rows = session.execute(
                select(WorkspaceOrm, WorkspaceMembershipOrm)
                .join(
                    WorkspaceMembershipOrm, WorkspaceMembershipOrm.workspace_id == WorkspaceOrm.id
                )
                .where(
                    WorkspaceMembershipOrm.user_id == user_id,
                    WorkspaceMembershipOrm.is_active.is_(True),
                    WorkspaceOrm.is_active.is_(True),
                )
                .order_by(WorkspaceOrm.name, WorkspaceOrm.id)
            ).all()
        return [
            (self._workspace(workspace), self._membership(member)) for workspace, member in rows
        ]

    def membership(self, *, workspace_id: str, user_id: str) -> WorkspaceMembershipItem | None:
        with self._db.session() as session:
            row = session.get(WorkspaceMembershipOrm, (workspace_id, user_id))
            workspace = session.get(WorkspaceOrm, workspace_id)
        if row is None or workspace is None or not row.is_active or not workspace.is_active:
            return None
        return self._membership(row)

    def session_membership(
        self, *, user_id: str, requested_workspace_id: str | None = None
    ) -> WorkspaceMembershipItem | None:
        """Resolve a browser session preference without treating it as an access grant."""
        self.ensure_home_membership(user_id=user_id)
        if requested_workspace_id is not None:
            return self.membership(workspace_id=requested_workspace_id, user_id=user_id)
        with self._db.session() as session:
            user = session.get(UserAccountOrm, user_id)
            if user is None or not user.is_active:
                return None
            preferred_workspace_id = user.last_workspace_id or user.workspace_id
        return self.membership(
            workspace_id=preferred_workspace_id, user_id=user_id
        ) or self.membership(workspace_id=self._default_workspace_id, user_id=user_id)

    def remember_selected_workspace(self, *, user_id: str, workspace_id: str) -> bool:
        """Persist an explicit, accessible selection independently of home identity."""
        with self._db.session() as session:
            user = session.get(UserAccountOrm, user_id)
            workspace = session.get(WorkspaceOrm, workspace_id)
            membership = session.get(WorkspaceMembershipOrm, (workspace_id, user_id))
            if (
                user is None
                or not user.is_active
                or workspace is None
                or not workspace.is_active
                or membership is None
                or not membership.is_active
            ):
                return False
            user.last_workspace_id = workspace_id
            session.commit()
        return True

    def list_memberships(self, *, workspace_id: str) -> list[WorkspaceMembershipItem]:
        with self._db.session() as session:
            rows = session.execute(
                select(WorkspaceMembershipOrm, UserAccountOrm)
                .join(UserAccountOrm, UserAccountOrm.id == WorkspaceMembershipOrm.user_id)
                .where(WorkspaceMembershipOrm.workspace_id == workspace_id)
                .order_by(WorkspaceMembershipOrm.created_at, WorkspaceMembershipOrm.user_id)
            ).all()
        return [self._membership(row, user=user) for row, user in rows]

    def upsert_membership(
        self,
        *,
        workspace_id: str,
        request: UpsertWorkspaceMembershipRequest,
        actor_user_id: str,
    ) -> WorkspaceMembershipItem:
        now = datetime.now(tz=UTC)
        requested_role = str(request.role)
        if workspace_id == self._default_workspace_id and (
            not request.is_active or requested_role == WorkspaceRole.VIEWER.value
        ):
            raise WorkspaceConflictError("default_workspace_membership_required")
        with self._db.session() as session:
            if self._lock_workspace(session, workspace_id) is None:
                raise LookupError("workspace_not_found")
            user = session.get(UserAccountOrm, request.user_id)
            if user is None:
                raise LookupError("user_not_found")
            row = session.get(WorkspaceMembershipOrm, (workspace_id, request.user_id))
            previous = None
            if row is None:
                row = WorkspaceMembershipOrm(
                    workspace_id=workspace_id,
                    user_id=request.user_id,
                    role=requested_role,
                    is_active=request.is_active,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                removes_owner = row.role == WorkspaceRole.OWNER.value and (
                    requested_role != WorkspaceRole.OWNER.value or not request.is_active
                )
                if removes_owner and self._active_owner_count(session, workspace_id) <= 1:
                    raise WorkspaceConflictError("last_workspace_owner_required")
                previous = {"role": row.role, "is_active": row.is_active}
                row.role = requested_role
                row.is_active = request.is_active
                row.updated_at = now
            current = {"role": requested_role, "is_active": request.is_active}
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type="membership",
                target_id=request.user_id,
                action="upserted",
                previous_value=previous,
                current_value=current,
                now=now,
            )
            session.commit()
            session.refresh(row)
            return self._membership(row, user=user)

    def revoke_membership(self, *, workspace_id: str, user_id: str, actor_user_id: str) -> bool:
        if workspace_id == self._default_workspace_id:
            raise WorkspaceConflictError("default_workspace_membership_required")
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            if self._lock_workspace(session, workspace_id) is None:
                return False
            row = session.get(WorkspaceMembershipOrm, (workspace_id, user_id))
            if row is None or not row.is_active:
                return False
            if (
                row.role == WorkspaceRole.OWNER.value
                and self._active_owner_count(session, workspace_id) <= 1
            ):
                raise WorkspaceConflictError("last_workspace_owner_required")
            previous = {"role": row.role, "is_active": row.is_active}
            row.is_active = False
            row.updated_at = now
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type="membership",
                target_id=user_id,
                action="revoked",
                previous_value=previous,
                current_value={"role": row.role, "is_active": False},
                now=now,
            )
            session.commit()
            return True

    def create_resource_grant(
        self, request: CreateWorkspaceResourceGrantRequest, *, actor_user_id: str
    ) -> WorkspaceResourceGrantItem:
        now = datetime.now(tz=UTC)
        kind = str(request.resource_kind)
        model = RESOURCE_MODELS[kind]
        with self._db.session() as session:
            recipient = self._lock_workspace(session, request.recipient_workspace_id)
            if recipient is None or not recipient.is_active:
                raise LookupError("workspace_not_found")
            source = session.get(model, request.resource_id)
            if (
                source is None
                or source.workspace_id != request.owner_workspace_id
                or not _resource_is_active(source, kind)
            ):
                raise LookupError("workspace_resource_not_found")
            local_alias = session.execute(
                select(model.id).where(
                    model.workspace_id == request.recipient_workspace_id,
                    model.name == request.alias,
                )
            ).first()
            if local_alias is not None:
                raise WorkspaceConflictError("workspace_resource_alias_conflict")
            existing = session.execute(
                select(WorkspaceResourceGrantOrm).where(
                    WorkspaceResourceGrantOrm.recipient_workspace_id
                    == request.recipient_workspace_id,
                    WorkspaceResourceGrantOrm.resource_kind == kind,
                    WorkspaceResourceGrantOrm.resource_id == request.resource_id,
                    WorkspaceResourceGrantOrm.owner_workspace_id == request.owner_workspace_id,
                )
            ).scalar_one_or_none()
            previous = None
            if existing is None:
                row = WorkspaceResourceGrantOrm(
                    id=str(uuid4()),
                    resource_kind=kind,
                    resource_id=request.resource_id,
                    owner_workspace_id=request.owner_workspace_id,
                    recipient_workspace_id=request.recipient_workspace_id,
                    alias=request.alias,
                    is_active=True,
                    created_by=actor_user_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row = existing
                previous = {"alias": row.alias, "is_active": row.is_active}
                row.alias = request.alias
                row.is_active = True
                row.updated_at = now
            self._audit(
                session,
                workspace_id=request.recipient_workspace_id,
                actor_user_id=actor_user_id,
                target_type="resource_grant",
                target_id=row.id,
                action="upserted",
                previous_value=previous,
                current_value={
                    "resource_kind": kind,
                    "resource_id": request.resource_id,
                    "owner_workspace_id": request.owner_workspace_id,
                    "alias": request.alias,
                    "is_active": True,
                },
                now=now,
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise WorkspaceConflictError("workspace_resource_alias_conflict") from exc
            session.refresh(row)
            return self._grant(row)

    def list_resource_grants(self, *, workspace_id: str) -> list[WorkspaceResourceGrantItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(WorkspaceResourceGrantOrm)
                    .where(WorkspaceResourceGrantOrm.recipient_workspace_id == workspace_id)
                    .order_by(
                        WorkspaceResourceGrantOrm.resource_kind, WorkspaceResourceGrantOrm.alias
                    )
                )
                .scalars()
                .all()
            )
        return [self._grant(row) for row in rows]

    def list_local_resources(self, *, workspace_id: str) -> list[WorkspaceResourceCatalogItem]:
        items: list[WorkspaceResourceCatalogItem] = []
        with self._db.session() as session:
            for kind, model in RESOURCE_MODELS.items():
                rows = (
                    session.execute(
                        select(model)
                        .where(
                            model.workspace_id == workspace_id,
                            _active_predicate(model, kind),
                        )
                        .order_by(model.name, model.id)
                    )
                    .scalars()
                    .all()
                )
                items.extend(
                    WorkspaceResourceCatalogItem(
                        id=row.id,
                        name=row.name,
                        kind=WorkspaceResourceKind(kind),
                        owner_workspace_id=workspace_id,
                    )
                    for row in rows
                )
        return items

    def revoke_resource_grant(
        self, *, grant_id: str, workspace_id: str, actor_user_id: str
    ) -> bool:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            if self._lock_workspace(session, workspace_id) is None:
                return False
            row = session.get(WorkspaceResourceGrantOrm, grant_id)
            if row is None or row.recipient_workspace_id != workspace_id or not row.is_active:
                return False
            row.is_active = False
            row.updated_at = now
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type="resource_grant",
                target_id=grant_id,
                action="revoked",
                previous_value={"alias": row.alias, "is_active": True},
                current_value={"alias": row.alias, "is_active": False},
                now=now,
            )
            session.commit()
            return True

    def resolve_resource(
        self,
        *,
        workspace_id: str,
        resource_kind: WorkspaceResourceKind | str,
        alias: str,
    ) -> ResolvedWorkspaceResource | None:
        kind = str(resource_kind)
        model = RESOURCE_MODELS[kind]
        with self._db.session() as session:
            local = session.execute(
                select(model).where(
                    model.workspace_id == workspace_id,
                    model.name == alias,
                    _active_predicate(model, kind),
                )
            ).scalar_one_or_none()
            if local is not None:
                return ResolvedWorkspaceResource(
                    resource_kind=WorkspaceResourceKind(kind),
                    resource_id=local.id,
                    owner_workspace_id=workspace_id,
                    requested_workspace_id=workspace_id,
                    alias=alias,
                    is_shared=False,
                )
            grant = session.execute(
                select(WorkspaceResourceGrantOrm).where(
                    WorkspaceResourceGrantOrm.recipient_workspace_id == workspace_id,
                    WorkspaceResourceGrantOrm.resource_kind == kind,
                    WorkspaceResourceGrantOrm.alias == alias,
                    WorkspaceResourceGrantOrm.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if grant is None:
                return None
            source = session.get(model, grant.resource_id)
            if (
                source is None
                or source.workspace_id != grant.owner_workspace_id
                or not _resource_is_active(source, kind)
            ):
                return None
            return ResolvedWorkspaceResource(
                resource_kind=WorkspaceResourceKind(kind),
                resource_id=grant.resource_id,
                owner_workspace_id=grant.owner_workspace_id,
                requested_workspace_id=workspace_id,
                alias=grant.alias,
                is_shared=True,
            )

    def resolve_resource_id(
        self,
        *,
        workspace_id: str,
        resource_kind: WorkspaceResourceKind | str,
        resource_id: str,
    ) -> ResolvedWorkspaceResource | None:
        """Resolve a stable ID without treating aliases as authorization."""
        kind = str(resource_kind)
        model = RESOURCE_MODELS[kind]
        with self._db.session() as session:
            source = session.get(model, resource_id)
            if source is None or not _resource_is_active(source, kind):
                return None
            if source.workspace_id == workspace_id:
                return ResolvedWorkspaceResource(
                    resource_kind=WorkspaceResourceKind(kind),
                    resource_id=resource_id,
                    owner_workspace_id=workspace_id,
                    requested_workspace_id=workspace_id,
                    alias=source.name,
                    is_shared=False,
                )
            grant = session.execute(
                select(WorkspaceResourceGrantOrm).where(
                    WorkspaceResourceGrantOrm.recipient_workspace_id == workspace_id,
                    WorkspaceResourceGrantOrm.resource_kind == kind,
                    WorkspaceResourceGrantOrm.resource_id == resource_id,
                    WorkspaceResourceGrantOrm.owner_workspace_id == source.workspace_id,
                    WorkspaceResourceGrantOrm.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if grant is None:
                return None
            return ResolvedWorkspaceResource(
                resource_kind=WorkspaceResourceKind(kind),
                resource_id=resource_id,
                owner_workspace_id=grant.owner_workspace_id,
                requested_workspace_id=workspace_id,
                alias=grant.alias,
                is_shared=True,
            )

    def list_audit(
        self, *, workspace_id: str, limit: int = 200
    ) -> list[WorkspacePolicyAuditEventItem]:
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(WorkspacePolicyAuditEventOrm)
                    .where(WorkspacePolicyAuditEventOrm.workspace_id == workspace_id)
                    .order_by(WorkspacePolicyAuditEventOrm.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._audit_item(row) for row in rows]

    @staticmethod
    def _ensure_default_assistant_policy(
        session: Session,
        *,
        workspace_id: str,
        actor_user_id: str | None,
        now: datetime,
    ) -> None:
        if session.get(DefaultAssistantPolicyOrm, workspace_id) is not None:
            return
        session.add(
            DefaultAssistantPolicyOrm(
                workspace_id=workspace_id,
                revision=1,
                policy_json={
                    "gitlab_write_scopes": [],
                    "allowed_subagent_ids": [],
                    "delegation_grants": [],
                    "max_iterations": 100,
                    "max_runtime_seconds": 1200,
                },
                created_by=actor_user_id,
                updated_by=actor_user_id,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _active_owner_count(session: Session, workspace_id: str) -> int:
        return len(
            session.execute(
                select(WorkspaceMembershipOrm.user_id).where(
                    WorkspaceMembershipOrm.workspace_id == workspace_id,
                    WorkspaceMembershipOrm.role == WorkspaceRole.OWNER.value,
                    WorkspaceMembershipOrm.is_active.is_(True),
                )
            ).all()
        )

    @staticmethod
    def _lock_workspace(session: Session, workspace_id: str) -> WorkspaceOrm | None:
        return session.execute(
            select(WorkspaceOrm).where(WorkspaceOrm.id == workspace_id).with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _audit(
        session: Session,
        *,
        workspace_id: str,
        actor_user_id: str,
        target_type: str,
        target_id: str,
        action: str,
        now: datetime,
        previous_value: dict[str, object] | None = None,
        current_value: dict[str, object] | None = None,
    ) -> None:
        session.add(
            WorkspacePolicyAuditEventOrm(
                id=str(uuid4()),
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                action=action,
                previous_value=previous_value,
                current_value=current_value,
                created_at=now,
            )
        )

    @staticmethod
    def _workspace(row: WorkspaceOrm) -> WorkspaceItem:
        return WorkspaceItem(
            id=row.id,
            name=row.name,
            is_active=row.is_active,
            metadata=row.metadata_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _membership(
        row: WorkspaceMembershipOrm,
        *,
        user: UserAccountOrm | None = None,
    ) -> WorkspaceMembershipItem:
        return WorkspaceMembershipItem(
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            user_login=user.login if user is not None else None,
            user_display_name=user.display_name if user is not None else None,
            role=WorkspaceRole(row.role),
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _grant(row: WorkspaceResourceGrantOrm) -> WorkspaceResourceGrantItem:
        return WorkspaceResourceGrantItem(
            id=row.id,
            resource_kind=WorkspaceResourceKind(row.resource_kind),
            resource_id=row.resource_id,
            owner_workspace_id=row.owner_workspace_id,
            recipient_workspace_id=row.recipient_workspace_id,
            alias=row.alias,
            is_active=row.is_active,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _audit_item(row: WorkspacePolicyAuditEventOrm) -> WorkspacePolicyAuditEventItem:
        return WorkspacePolicyAuditEventItem(
            id=row.id,
            workspace_id=row.workspace_id,
            actor_user_id=row.actor_user_id,
            target_type=row.target_type,
            target_id=row.target_id,
            action=row.action,
            previous_value=row.previous_value,
            current_value=row.current_value,
            created_at=row.created_at,
        )
