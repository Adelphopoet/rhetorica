"""PostgreSQL adapter for file artifact ownership and lifecycle."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from rhetorica_backend.domain.files.models import Artifact, ArtifactSource, ArtifactStatus
from rhetorica_backend.domain.files.policy import ArtifactRejectedError
from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm, ChatMessageOrm
from rhetorica_backend.infra.postgres.file_models import (
    ChatMessageFileOrm,
    FileArtifactOrm,
    FileQuotaUsageOrm,
)
from rhetorica_backend.infra.postgres.scenario_workflow_models import ScenarioArtifactOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session


class FileArtifactRepository:
    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    def create(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        storage_key: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        source: ArtifactSource,
        status: ArtifactStatus,
        source_artifact_ids: Iterable[str] = (),
    ) -> Artifact:
        now = datetime.now(tz=UTC)
        lineage_ids = list(dict.fromkeys(source_artifact_ids))
        with self._db.session() as session:
            conversation = session.execute(
                select(ChatConversationOrm.id).where(
                    ChatConversationOrm.id == conversation_id,
                    ChatConversationOrm.workspace_id == workspace_id,
                    ChatConversationOrm.owner_user_id == owner_user_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ValueError("Conversation ownership mismatch")
            if lineage_ids:
                lineage = set(
                    session.execute(
                        select(FileArtifactOrm.id).where(
                            FileArtifactOrm.id.in_(lineage_ids),
                            FileArtifactOrm.conversation_id == conversation_id,
                            FileArtifactOrm.owner_user_id == owner_user_id,
                            FileArtifactOrm.status == ArtifactStatus.READY.value,
                        )
                    ).scalars()
                )
                if lineage != set(lineage_ids):
                    raise ValueError("Artifact lineage ownership mismatch")
            row = FileArtifactOrm(
                id=artifact_id,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                storage_key=storage_key,
                filename=filename,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
                source=source.value,
                status=status.value,
                source_artifact_ids=lineage_ids,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            self._increment_usage_unbounded(
                session,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                incoming=size_bytes,
                now=now,
            )
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def reserve(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        storage_key: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        source: ArtifactSource,
        source_artifact_ids: Iterable[str] = (),
        user_quota_bytes: int,
        workspace_quota_bytes: int,
    ) -> Artifact:
        """Atomically reserve user/workspace quota before object upload."""
        now = datetime.now(tz=UTC)
        lineage_ids = list(dict.fromkeys(source_artifact_ids))
        with self._db.session() as session:
            self._validate_ownership(
                session,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                lineage_ids=lineage_ids,
            )
            self._increment_quota_or_reject(
                session,
                scope_type="workspace",
                scope_id=workspace_id,
                incoming=size_bytes,
                limit=workspace_quota_bytes,
                error_code="workspace_quota_exceeded",
                error_message="Workspace file quota exceeded",
                now=now,
            )
            self._increment_quota_or_reject(
                session,
                scope_type="user",
                scope_id=owner_user_id,
                incoming=size_bytes,
                limit=user_quota_bytes,
                error_code="user_quota_exceeded",
                error_message="User file quota exceeded",
                now=now,
            )
            row = FileArtifactOrm(
                id=artifact_id,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                storage_key=storage_key,
                filename=filename,
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
                source=source.value,
                status=ArtifactStatus.RESERVED.value,
                source_artifact_ids=lineage_ids,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def finalize_reservation(self, artifact_id: str) -> Artifact:
        with self._db.session() as session:
            row = session.execute(
                select(FileArtifactOrm).where(
                    FileArtifactOrm.id == artifact_id,
                    FileArtifactOrm.status == ArtifactStatus.RESERVED.value,
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("Artifact reservation does not exist")
            row.status = ArtifactStatus.STAGED.value
            row.updated_at = datetime.now(tz=UTC)
            session.commit()
            session.refresh(row)
            return self._to_domain(row)

    def cancel_reservation(self, artifact_id: str) -> None:
        with self._db.session() as session:
            row = session.get(FileArtifactOrm, artifact_id)
            if row is None:
                return
            self._release_usage(session, row)
            session.delete(row)
            session.commit()

    def get_owned(self, artifact_id: str, owner_user_id: str, workspace_id: str) -> Artifact | None:
        with self._db.session() as session:
            row = session.execute(
                select(FileArtifactOrm).where(
                    FileArtifactOrm.id == artifact_id,
                    FileArtifactOrm.owner_user_id == owner_user_id,
                    FileArtifactOrm.workspace_id == workspace_id,
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row is not None else None

    def list_conversation(
        self, *, workspace_id: str, conversation_id: str, owner_user_id: str, limit: int = 100
    ) -> list[Artifact]:
        bounded_limit = max(1, min(limit, 200))
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(FileArtifactOrm)
                    .where(
                        FileArtifactOrm.conversation_id == conversation_id,
                        FileArtifactOrm.owner_user_id == owner_user_id,
                        FileArtifactOrm.workspace_id == workspace_id,
                        FileArtifactOrm.status.in_([
                            ArtifactStatus.STAGED.value,
                            ArtifactStatus.READY.value,
                        ]),
                    )
                    .order_by(FileArtifactOrm.created_at.asc())
                    .limit(bounded_limit)
                )
                .scalars()
                .all()
            )
        return [self._to_domain(row) for row in rows]

    def list_message_refs(self, message_ids: Iterable[str]) -> dict[str, list[Artifact]]:
        ids = list(dict.fromkeys(message_ids))
        if not ids:
            return {}
        with self._db.session() as session:
            rows = session.execute(
                select(ChatMessageFileOrm, FileArtifactOrm)
                .join(FileArtifactOrm, FileArtifactOrm.id == ChatMessageFileOrm.artifact_id)
                .where(
                    ChatMessageFileOrm.message_id.in_(ids),
                    FileArtifactOrm.status == ArtifactStatus.READY.value,
                )
                .order_by(ChatMessageFileOrm.message_id, ChatMessageFileOrm.position)
            ).all()
        result: dict[str, list[Artifact]] = {item: [] for item in ids}
        for link, artifact in rows:
            result.setdefault(link.message_id, []).append(self._to_domain(artifact))
        return result

    def usage_bytes(self, *, workspace_id: str, owner_user_id: str | None = None) -> int:
        with self._db.session() as session:
            stmt = select(func.coalesce(func.sum(FileArtifactOrm.size_bytes), 0)).where(
                FileArtifactOrm.workspace_id == workspace_id,
            )
            if owner_user_id is not None:
                stmt = stmt.where(FileArtifactOrm.owner_user_id == owner_user_id)
            return int(session.execute(stmt).scalar_one())

    def attach_to_message(
        self,
        *,
        artifact_ids: Iterable[str],
        message_id: str,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        relation: str,
    ) -> list[Artifact]:
        ids = list(dict.fromkeys(artifact_ids))
        if relation not in {"input", "output"}:
            raise ValueError("Invalid artifact relation")
        if not ids:
            return []
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            message_exists = session.execute(
                select(ChatMessageOrm.id)
                .join(ChatConversationOrm, ChatConversationOrm.id == ChatMessageOrm.conversation_id)
                .where(
                    ChatMessageOrm.id == message_id,
                    ChatMessageOrm.conversation_id == conversation_id,
                    ChatConversationOrm.owner_user_id == owner_user_id,
                    ChatConversationOrm.workspace_id == workspace_id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if message_exists is None:
                raise ValueError("Message ownership mismatch")
            rows = list(
                session.execute(
                    select(FileArtifactOrm)
                    .where(
                        FileArtifactOrm.id.in_(ids),
                        FileArtifactOrm.conversation_id == conversation_id,
                        FileArtifactOrm.owner_user_id == owner_user_id,
                        FileArtifactOrm.workspace_id == workspace_id,
                        FileArtifactOrm.status.in_([
                            ArtifactStatus.STAGED.value,
                            ArtifactStatus.READY.value,
                        ]),
                    )
                    .order_by(FileArtifactOrm.id)
                    .with_for_update()
                ).scalars()
            )
            by_id = {row.id: row for row in rows}
            if set(by_id) != set(ids):
                raise ValueError("Artifact ownership mismatch")
            for position, artifact_id in enumerate(ids):
                existing = session.execute(
                    select(ChatMessageFileOrm.id).where(
                        ChatMessageFileOrm.message_id == message_id,
                        ChatMessageFileOrm.artifact_id == artifact_id,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        ChatMessageFileOrm(
                            message_id=message_id,
                            artifact_id=artifact_id,
                            relation=relation,
                            position=position,
                            created_at=now,
                        )
                    )
                by_id[artifact_id].status = ArtifactStatus.READY.value
                by_id[artifact_id].updated_at = now
            session.commit()
            return [self._to_domain(by_id[item]) for item in ids]

    def delete_staged(
        self, *, artifact_id: str, owner_user_id: str, workspace_id: str
    ) -> Artifact | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(FileArtifactOrm)
                .where(
                    FileArtifactOrm.id == artifact_id,
                    FileArtifactOrm.owner_user_id == owner_user_id,
                    FileArtifactOrm.workspace_id == workspace_id,
                    FileArtifactOrm.status == ArtifactStatus.STAGED.value,
                    ~FileArtifactOrm.id.in_(select(ChatMessageFileOrm.artifact_id)),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = ArtifactStatus.DELETING.value
            row.updated_at = now
            session.commit()
            return self._to_domain(row)

    def mark_failed(self, artifact_id: str) -> None:
        self._set_status(artifact_id, ArtifactStatus.FAILED)

    def claim_cleanup(self, *, staged_before: datetime, limit: int = 100) -> list[Artifact]:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            rows = list(
                session.execute(
                    select(FileArtifactOrm)
                    .where(~FileArtifactOrm.id.in_(select(ScenarioArtifactOrm.artifact_id)))
                    .where(
                        (FileArtifactOrm.status == ArtifactStatus.DELETING.value)
                        | (
                            (
                                FileArtifactOrm.status.in_([
                                    ArtifactStatus.RESERVED.value,
                                    ArtifactStatus.STAGED.value,
                                    ArtifactStatus.FAILED.value,
                                ])
                            )
                            & (FileArtifactOrm.created_at < staged_before)
                        )
                    )
                    .order_by(FileArtifactOrm.created_at.asc())
                    .limit(max(1, min(limit, 1000)))
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            for row in rows:
                row.status = ArtifactStatus.DELETING.value
                row.updated_at = now
            session.commit()
            return [self._to_domain(row) for row in rows]

    def finish_cleanup(self, artifact_id: str) -> None:
        with self._db.session() as session:
            row = session.get(FileArtifactOrm, artifact_id)
            if row is not None:
                self._release_usage(session, row)
                session.delete(row)
            session.commit()

    def retry_cleanup(self, artifact_id: str) -> None:
        self._set_status(artifact_id, ArtifactStatus.DELETING)

    def _set_status(self, artifact_id: str, status: ArtifactStatus) -> None:
        with self._db.session() as session:
            row = session.get(FileArtifactOrm, artifact_id)
            if row is not None:
                row.status = status.value
                row.updated_at = datetime.now(tz=UTC)
                session.commit()

    @staticmethod
    def _validate_ownership(
        session: Session,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        lineage_ids: list[str],
    ) -> None:
        conversation = session.execute(
            select(ChatConversationOrm.id).where(
                ChatConversationOrm.id == conversation_id,
                ChatConversationOrm.workspace_id == workspace_id,
                ChatConversationOrm.owner_user_id == owner_user_id,
                ChatConversationOrm.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise ValueError("Conversation ownership mismatch")
        if not lineage_ids:
            return
        lineage = set(
            session.execute(
                select(FileArtifactOrm.id).where(
                    FileArtifactOrm.id.in_(lineage_ids),
                    FileArtifactOrm.conversation_id == conversation_id,
                    FileArtifactOrm.owner_user_id == owner_user_id,
                    FileArtifactOrm.status == ArtifactStatus.READY.value,
                )
            ).scalars()
        )
        if lineage != set(lineage_ids):
            raise ValueError("Artifact lineage ownership mismatch")

    @classmethod
    def _increment_quota_or_reject(
        cls,
        session: Session,
        *,
        scope_type: str,
        scope_id: str,
        incoming: int,
        limit: int,
        error_code: str,
        error_message: str,
        now: datetime,
    ) -> None:
        if incoming > limit:
            raise ArtifactRejectedError(error_code, error_message)
        cls._ensure_usage_row(
            session,
            scope_type=scope_type,
            scope_id=scope_id,
            now=now,
        )
        result = session.execute(
            update(FileQuotaUsageOrm)
            .where(
                FileQuotaUsageOrm.scope_type == scope_type,
                FileQuotaUsageOrm.scope_id == scope_id,
                FileQuotaUsageOrm.used_bytes <= limit - incoming,
            )
            .values(
                used_bytes=FileQuotaUsageOrm.used_bytes + incoming,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ArtifactRejectedError(error_code, error_message)

    @classmethod
    def _increment_usage_unbounded(
        cls,
        session: Session,
        *,
        workspace_id: str,
        owner_user_id: str,
        incoming: int,
        now: datetime,
    ) -> None:
        for scope_type, scope_id in (
            ("workspace", workspace_id),
            ("user", owner_user_id),
        ):
            cls._ensure_usage_row(
                session,
                scope_type=scope_type,
                scope_id=scope_id,
                now=now,
            )
            session.execute(
                update(FileQuotaUsageOrm)
                .where(
                    FileQuotaUsageOrm.scope_type == scope_type,
                    FileQuotaUsageOrm.scope_id == scope_id,
                )
                .values(
                    used_bytes=FileQuotaUsageOrm.used_bytes + incoming,
                    updated_at=now,
                )
            )

    @staticmethod
    def _ensure_usage_row(
        session: Session,
        *,
        scope_type: str,
        scope_id: str,
        now: datetime,
    ) -> None:
        values = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "used_bytes": 0,
            "updated_at": now,
        }
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "postgresql":
            statement = postgresql_insert(FileQuotaUsageOrm).values(**values)
            session.execute(
                statement.on_conflict_do_nothing(index_elements=["scope_type", "scope_id"])
            )
            return
        if dialect == "sqlite":
            session.execute(
                sqlite_insert(FileQuotaUsageOrm)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["scope_type", "scope_id"])
            )
            return
        if session.get(FileQuotaUsageOrm, (scope_type, scope_id)) is None:
            session.add(FileQuotaUsageOrm(**values))
            session.flush()

    @classmethod
    def _release_usage(cls, session: Session, artifact: FileArtifactOrm) -> None:
        now = datetime.now(tz=UTC)
        for scope_type, scope_id in (
            ("workspace", artifact.workspace_id),
            ("user", artifact.owner_user_id),
        ):
            session.execute(
                update(FileQuotaUsageOrm)
                .where(
                    FileQuotaUsageOrm.scope_type == scope_type,
                    FileQuotaUsageOrm.scope_id == scope_id,
                )
                .values(
                    used_bytes=case(
                        (
                            FileQuotaUsageOrm.used_bytes >= artifact.size_bytes,
                            FileQuotaUsageOrm.used_bytes - artifact.size_bytes,
                        ),
                        else_=0,
                    ),
                    updated_at=now,
                )
            )

    @staticmethod
    def _to_domain(row: FileArtifactOrm) -> Artifact:
        return Artifact(
            id=row.id,
            workspace_id=row.workspace_id,
            owner_user_id=row.owner_user_id,
            conversation_id=row.conversation_id,
            storage_key=row.storage_key,
            filename=row.filename,
            media_type=row.media_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            source=ArtifactSource(row.source),
            status=ArtifactStatus(row.status),
            source_artifact_ids=tuple(row.source_artifact_ids or ()),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
