from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillItem,
    WorkspaceSkillVersionSnapshot,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.models import WorkspaceSkillOrm, WorkspaceSkillVersionOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_resource_access import (
    AccessibleResource,
    WorkspaceResourceAccess,
    present_resource,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class WorkspaceSkillRepository:
    """Persistence gateway for workspace skill snapshots."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db
        self._resources = WorkspaceResourceAccess(
            db,
            kind=WorkspaceResourceKind.WORKSPACE_SKILL,
            model=WorkspaceSkillOrm,
            is_active=lambda row: row.status == "active",
        )

    def create(
        self,
        *,
        workspace_id: str,
        name: str,
        capability_family: str,
        engine_scope: str,
        priority: int,
        source_kind: str,
        source_url: str | None,
        status: str,
        validation_status: str,
        visible_to_default_assistant: bool,
        title: str | None,
        summary: str | None,
        content_markdown: str,
        content_bundle: dict[str, object],
        content_sha256: str | None,
        version_digest: str,
        last_validated_at: datetime | None,
    ) -> WorkspaceSkillItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = WorkspaceSkillOrm(
                id=str(uuid4()),
                workspace_id=workspace_id,
                name=name,
                capability_family=capability_family,
                engine_scope=engine_scope,
                priority=priority,
                source_kind=source_kind,
                source_url=source_url,
                status=status,
                validation_status=validation_status,
                visible_to_default_assistant=visible_to_default_assistant,
                title=title,
                summary=summary,
                content_markdown=content_markdown,
                content_bundle=content_bundle,
                content_sha256=content_sha256,
                current_version_digest=version_digest,
                last_validated_at=last_validated_at,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.add(
                WorkspaceSkillVersionOrm(
                    skill_id=row.id,
                    workspace_id=workspace_id,
                    version_digest=version_digest,
                    source_url=source_url,
                    content_markdown=content_markdown,
                    content_bundle=content_bundle,
                    created_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def update(
        self,
        *,
        skill_id: str,
        workspace_id: str,
        name: str,
        capability_family: str,
        engine_scope: str,
        priority: int,
        source_kind: str,
        source_url: str | None,
        status: str,
        validation_status: str,
        visible_to_default_assistant: bool,
        title: str | None,
        summary: str | None,
        content_markdown: str,
        content_bundle: dict[str, object],
        content_sha256: str | None,
        version_digest: str,
        last_validated_at: datetime | None,
    ) -> WorkspaceSkillItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(WorkspaceSkillOrm, skill_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.name = name
            row.capability_family = capability_family
            row.engine_scope = engine_scope
            row.priority = priority
            row.source_kind = source_kind
            row.source_url = source_url
            row.status = status
            row.validation_status = validation_status
            row.visible_to_default_assistant = visible_to_default_assistant
            row.title = title
            row.summary = summary
            row.content_markdown = content_markdown
            row.content_bundle = content_bundle
            row.content_sha256 = content_sha256
            row.current_version_digest = version_digest
            row.last_validated_at = last_validated_at
            row.updated_at = now
            existing_version = session.scalar(
                select(WorkspaceSkillVersionOrm.id).where(
                    WorkspaceSkillVersionOrm.skill_id == skill_id,
                    WorkspaceSkillVersionOrm.version_digest == version_digest,
                )
            )
            if existing_version is None:
                session.add(
                    WorkspaceSkillVersionOrm(
                        skill_id=skill_id,
                        workspace_id=workspace_id,
                        version_digest=version_digest,
                        source_url=source_url,
                        content_markdown=content_markdown,
                        content_bundle=content_bundle,
                        created_at=now,
                    )
                )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[WorkspaceSkillItem]:
        return [
            present_resource(self._to_contract(resource.row), resource)
            for resource in self._sorted_resources(workspace_id=workspace_id)[:limit]
        ]

    def list_active_for_capability(
        self,
        *,
        workspace_id: str,
        capability_family: str,
        engine_scope: str,
        limit: int = 20,
    ) -> list[WorkspaceSkillItem]:
        resources = [
            item
            for item in self._sorted_resources(workspace_id=workspace_id)
            if item.row.capability_family == capability_family
            and item.row.status == "active"
            and item.row.validation_status == "valid"
            and item.row.engine_scope in {engine_scope, "any"}
        ][:limit]
        return [present_resource(self._to_contract(item.row), item) for item in resources]

    def list_active_chat_instructions(
        self,
        *,
        workspace_id: str,
        visible_to_default_assistant: bool | None = None,
        skill_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[WorkspaceSkillItem]:
        normalized_ids = self._normalize_skill_ids(skill_ids)
        if skill_ids is not None and not normalized_ids:
            return []
        resources = [
            item
            for item in self._sorted_resources(workspace_id=workspace_id)
            if item.row.capability_family == "chat_instruction"
            and item.row.status == "active"
            and item.row.validation_status == "valid"
            and (
                visible_to_default_assistant is None
                or item.row.visible_to_default_assistant is visible_to_default_assistant
            )
            and (normalized_ids is None or item.row.id in normalized_ids)
        ][:limit]
        return [present_resource(self._to_contract(item.row), item) for item in resources]

    def get_active_chat_instruction(
        self,
        *,
        workspace_id: str,
        skill_id: str | None = None,
        skill_name: str | None = None,
        visible_to_default_assistant: bool | None = None,
        skill_ids: list[str] | None = None,
    ) -> WorkspaceSkillItem | None:
        normalized_id = (skill_id or "").strip()
        normalized_name = (skill_name or "").strip()
        if not normalized_id and not normalized_name:
            return None
        normalized_ids = self._normalize_skill_ids(skill_ids)
        if skill_ids is not None and not normalized_ids:
            return None

        resource = next(
            (
                item
                for item in self._sorted_resources(workspace_id=workspace_id)
                if item.row.capability_family == "chat_instruction"
                and item.row.status == "active"
                and item.row.validation_status == "valid"
                and (
                    visible_to_default_assistant is None
                    or item.row.visible_to_default_assistant is visible_to_default_assistant
                )
                and (normalized_ids is None or item.row.id in normalized_ids)
                and (
                    item.row.id == normalized_id if normalized_id else item.alias == normalized_name
                )
            ),
            None,
        )
        if resource is None:
            return None
        return present_resource(self._to_contract(resource.row), resource)

    def _sorted_resources(
        self,
        *,
        workspace_id: str,
    ) -> list[AccessibleResource[WorkspaceSkillOrm]]:
        return sorted(
            self._resources.list(workspace_id=workspace_id, limit=None),
            key=lambda item: (
                -item.row.priority,
                -item.row.updated_at.timestamp(),
                item.alias,
            ),
        )

    def delete(self, skill_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(WorkspaceSkillOrm, skill_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            session.delete(row)
            session.commit()
            return True

    def get_version(
        self,
        *,
        skill_id: str,
        workspace_id: str,
        version_digest: str,
    ) -> WorkspaceSkillVersionSnapshot | None:
        """Return a retained immutable version after caller has checked live access."""
        with self._db.session() as session:
            row = session.scalar(
                select(WorkspaceSkillVersionOrm).where(
                    WorkspaceSkillVersionOrm.skill_id == skill_id,
                    WorkspaceSkillVersionOrm.workspace_id == workspace_id,
                    WorkspaceSkillVersionOrm.version_digest == version_digest,
                )
            )
            if row is None:
                return None
            return WorkspaceSkillVersionSnapshot(
                skill_id=row.skill_id,
                workspace_id=row.workspace_id,
                version_digest=row.version_digest,
                source_url=row.source_url,
                content_markdown=row.content_markdown,
                content_bundle=row.content_bundle or {},
            )

    @staticmethod
    def _to_contract(row: WorkspaceSkillOrm) -> WorkspaceSkillItem:
        return WorkspaceSkillItem(
            id=row.id,
            workspace_id=row.workspace_id,
            name=row.name,
            capability_family=row.capability_family,
            engine_scope=row.engine_scope,
            priority=row.priority,
            source_kind=row.source_kind,
            source_url=row.source_url,
            status=row.status,
            validation_status=row.validation_status,
            visible_to_default_assistant=bool(getattr(row, "visible_to_default_assistant", True)),
            title=row.title,
            summary=row.summary,
            content_markdown=row.content_markdown,
            content_bundle=row.content_bundle or {},
            content_sha256=row.content_sha256,
            version_digest=getattr(row, "current_version_digest", None),
            last_validated_at=row.last_validated_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _normalize_skill_ids(skill_ids: list[str] | None) -> list[str] | None:
        if skill_ids is None:
            return None
        seen: set[str] = set()
        normalized: list[str] = []
        for value in skill_ids:
            candidate = str(value).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized
