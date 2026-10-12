from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillItem
from rhetorica_backend.infra.postgres.models import WorkspaceSkillOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


class WorkspaceSkillRepository:
    """Persistence gateway for workspace skill snapshots."""

    def __init__(self, db: DatabaseRuntime):
        self._db = db

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
                last_validated_at=last_validated_at,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
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
            row.last_validated_at = last_validated_at
            row.updated_at = now
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row)

    def list(self, workspace_id: str, limit: int) -> list[WorkspaceSkillItem]:
        with self._db.session() as session:
            stmt = (
                select(WorkspaceSkillOrm)
                .where(WorkspaceSkillOrm.workspace_id == workspace_id)
                .order_by(
                    WorkspaceSkillOrm.priority.desc(),
                    WorkspaceSkillOrm.updated_at.desc(),
                    WorkspaceSkillOrm.name.asc(),
                )
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        return [self._to_contract(row) for row in rows]

    def list_active_for_capability(
        self,
        *,
        workspace_id: str,
        capability_family: str,
        engine_scope: str,
        limit: int = 20,
    ) -> list[WorkspaceSkillItem]:
        with self._db.session() as session:
            stmt = (
                select(WorkspaceSkillOrm)
                .where(
                    WorkspaceSkillOrm.workspace_id == workspace_id,
                    WorkspaceSkillOrm.capability_family == capability_family,
                    WorkspaceSkillOrm.status == "active",
                    WorkspaceSkillOrm.validation_status == "valid",
                    WorkspaceSkillOrm.engine_scope.in_([engine_scope, "any"]),
                )
                .order_by(
                    WorkspaceSkillOrm.priority.desc(),
                    WorkspaceSkillOrm.updated_at.desc(),
                    WorkspaceSkillOrm.name.asc(),
                )
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        return [self._to_contract(row) for row in rows]

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
        with self._db.session() as session:
            conditions = [
                WorkspaceSkillOrm.workspace_id == workspace_id,
                WorkspaceSkillOrm.capability_family == "chat_instruction",
                WorkspaceSkillOrm.status == "active",
                WorkspaceSkillOrm.validation_status == "valid",
            ]
            if visible_to_default_assistant is not None:
                conditions.append(
                    WorkspaceSkillOrm.visible_to_default_assistant.is_(visible_to_default_assistant)
                )
            if normalized_ids is not None:
                conditions.append(WorkspaceSkillOrm.id.in_(normalized_ids))
            stmt = (
                select(WorkspaceSkillOrm)
                .where(*conditions)
                .order_by(
                    WorkspaceSkillOrm.priority.desc(),
                    WorkspaceSkillOrm.updated_at.desc(),
                    WorkspaceSkillOrm.name.asc(),
                )
                .limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
        return [self._to_contract(row) for row in rows]

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

        with self._db.session() as session:
            conditions = [
                WorkspaceSkillOrm.workspace_id == workspace_id,
                WorkspaceSkillOrm.capability_family == "chat_instruction",
                WorkspaceSkillOrm.status == "active",
                WorkspaceSkillOrm.validation_status == "valid",
            ]
            if visible_to_default_assistant is not None:
                conditions.append(
                    WorkspaceSkillOrm.visible_to_default_assistant.is_(visible_to_default_assistant)
                )
            if normalized_ids is not None:
                conditions.append(WorkspaceSkillOrm.id.in_(normalized_ids))
            if normalized_id:
                conditions.append(WorkspaceSkillOrm.id == normalized_id)
            else:
                conditions.append(WorkspaceSkillOrm.name == normalized_name)
            stmt = select(WorkspaceSkillOrm).where(*conditions).limit(1)
            row = session.execute(stmt).scalar_one_or_none()
        return self._to_contract(row) if row is not None else None

    def delete(self, skill_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(WorkspaceSkillOrm, skill_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            session.delete(row)
            session.commit()
            return True

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
