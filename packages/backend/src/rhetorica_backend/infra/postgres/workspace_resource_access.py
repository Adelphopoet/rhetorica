"""Shared live resolution for workspace-owned and explicitly granted resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from pydantic import BaseModel
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from sqlalchemy import select


class ResourceRow(Protocol):
    id: str
    workspace_id: str
    name: str
    updated_at: datetime


@dataclass(frozen=True)
class AccessibleResource[T: ResourceRow]:
    row: T
    alias: str
    requested_workspace_id: str
    is_shared: bool


class WorkspaceResourceAccess[T: ResourceRow]:
    """Resolve resources without copying their records or credentials."""

    def __init__(
        self,
        db: DatabaseRuntime,
        *,
        kind: WorkspaceResourceKind,
        model: type[T],
        is_active: Callable[[T], bool] | None = None,
    ) -> None:
        self._db = db
        self._kind = kind
        self._model: Any = model
        self._is_active = is_active or (lambda row: bool(getattr(row, "is_active", False)))
        self._workspaces = WorkspaceRepository(db)

    def list(self, *, workspace_id: str, limit: int | None) -> list[AccessibleResource[T]]:
        with self._db.session() as session:
            local_rows = cast(
                list[T],
                session.execute(
                    select(self._model)
                    .where(self._model.workspace_id == workspace_id)
                    .order_by(self._model.updated_at.desc())
                )
                .scalars()
                .all(),
            )
        resources = [
            AccessibleResource(
                row=row,
                alias=row.name,
                requested_workspace_id=workspace_id,
                is_shared=False,
            )
            for row in local_rows
        ]
        local_names = {resource.alias for resource in resources}
        for grant in self._workspaces.list_resource_grants(workspace_id=workspace_id):
            if (
                not grant.is_active
                or grant.resource_kind != self._kind
                or grant.alias in local_names
            ):
                continue
            resolved = self._workspaces.resolve_resource(
                workspace_id=workspace_id,
                resource_kind=self._kind,
                alias=grant.alias,
            )
            if resolved is None:
                continue
            with self._db.session() as session:
                row = session.get(self._model, resolved.resource_id)
            if row is None:
                continue
            resources.append(
                AccessibleResource(
                    row=row,
                    alias=resolved.alias,
                    requested_workspace_id=workspace_id,
                    is_shared=True,
                )
            )
        return resources[:limit] if limit is not None else resources

    def by_name(
        self,
        *,
        workspace_id: str,
        name: str,
    ) -> AccessibleResource[T] | None:
        resolved = self._workspaces.resolve_resource(
            workspace_id=workspace_id,
            resource_kind=self._kind,
            alias=name,
        )
        if resolved is None:
            return None
        with self._db.session() as session:
            row = session.get(self._model, resolved.resource_id)
        if row is None:
            return None
        return AccessibleResource(
            row=row,
            alias=resolved.alias,
            requested_workspace_id=workspace_id,
            is_shared=resolved.is_shared,
        )

    def by_id(
        self,
        *,
        workspace_id: str,
        resource_id: str,
    ) -> AccessibleResource[T] | None:
        for resource in self.list(workspace_id=workspace_id, limit=None):
            if resource.row.id == resource_id:
                return resource
        return None

    def first_active(
        self,
        *,
        workspace_id: str,
        name: str | None = None,
    ) -> AccessibleResource[T] | None:
        if name:
            resource = self.by_name(workspace_id=workspace_id, name=name)
            if resource is None or not self._is_active(resource.row):
                return None
            return resource
        return next(
            (
                resource
                for resource in self.list(workspace_id=workspace_id, limit=None)
                if self._is_active(resource.row)
            ),
            None,
        )


def present_resource[M: BaseModel, T: ResourceRow](
    item: M,
    resource: AccessibleResource[T],
    **updates: object,
) -> M:
    """Present a source-owned contract under its recipient alias."""
    return cast(
        M,
        item.model_copy(
            update={
                "name": resource.alias,
                "workspace_id": resource.requested_workspace_id,
                **updates,
            }
        ),
    )
