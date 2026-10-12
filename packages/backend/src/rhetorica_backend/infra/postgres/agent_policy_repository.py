"""Atomic storage and validation for versioned governed agent policies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError
from rhetorica_backend.contracts.coding import (
    AgentGovernedPolicy,
    AgentPolicyRecord,
    DefaultAssistantPolicy,
    GovernedAgentSettings,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.domain.coding.policies import (
    DEFAULT_ASSISTANT_POLICY_ID,
    AdminAgentPolicyService,
    AgentPolicySubject,
    AgentPolicyValidationError,
)
from rhetorica_backend.infra.postgres.coding_policy_models import (
    AgentGovernedPolicyOrm,
    DefaultAssistantPolicyOrm,
)
from rhetorica_backend.infra.postgres.models import AgentProfileOrm, GitLabConnectorConfigOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_models import (
    WorkspaceOrm,
    WorkspacePolicyAuditEventOrm,
    WorkspaceResourceGrantOrm,
)
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session


class AgentPolicyConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("policy_revision_conflict")


class AgentPolicyRepository:
    """One repository owns policy locking, CAS and audit transaction boundaries."""

    def __init__(
        self,
        db: DatabaseRuntime,
        *,
        validator: AdminAgentPolicyService | None = None,
    ) -> None:
        self._db = db
        self._validator = validator or AdminAgentPolicyService()

    def get_agent_policy(self, *, workspace_id: str, agent_id: str) -> AgentGovernedPolicy | None:
        with self._db.session() as session:
            agent = session.get(AgentProfileOrm, agent_id)
            if agent is None or agent.workspace_id != workspace_id:
                return None
            row = session.get(AgentGovernedPolicyOrm, agent_id)
            settings = self._settings(row) if row is not None else GovernedAgentSettings()
            return AgentGovernedPolicy(
                workspace_id=workspace_id,
                agent_id=agent_id,
                **settings.model_dump(),
            )

    def list_agent_policies(self, *, workspace_id: str) -> list[AgentGovernedPolicy]:
        with self._db.session() as session:
            agents = list(
                session.execute(
                    select(AgentProfileOrm).where(AgentProfileOrm.workspace_id == workspace_id)
                ).scalars()
            )
            rows = {
                row.agent_id: row
                for row in session.execute(
                    select(AgentGovernedPolicyOrm).where(
                        AgentGovernedPolicyOrm.workspace_id == workspace_id
                    )
                ).scalars()
            }
            return [
                AgentGovernedPolicy(
                    workspace_id=workspace_id,
                    agent_id=agent.id,
                    **self._settings(rows.get(agent.id)).model_dump(),
                )
                for agent in agents
            ]

    def replace_agent_policy(
        self,
        *,
        workspace_id: str,
        agent_id: str,
        actor_user_id: str,
        settings: GovernedAgentSettings,
    ) -> AgentGovernedPolicy:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            self._lock_workspace(session, workspace_id)
            agent = session.get(AgentProfileOrm, agent_id)
            if agent is None or agent.workspace_id != workspace_id:
                raise LookupError("agent_policy_target_not_found")
            row = self._ensure_agent_policy(
                session, agent=agent, actor_user_id=actor_user_id, now=now
            )
            if row.revision != settings.revision:
                raise AgentPolicyConflictError(row.revision)
            subjects, records, accessible_ids = self._validation_state(
                session, workspace_id=workspace_id
            )
            self._validator.validate_agent_update(
                workspace_id=workspace_id,
                target_agent_id=agent_id,
                requested=settings,
                subjects=subjects,
                current_policies=records,
                accessible_connector_ids=accessible_ids,
            )
            previous = self._settings(row)
            updated_revision = self._cas_agent_policy(
                session,
                row=row,
                settings=settings,
                actor_user_id=actor_user_id,
                now=now,
            )
            current = settings.model_copy(update={"revision": updated_revision})
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type="agent_governed_policy",
                target_id=agent_id,
                previous=previous,
                current=current,
                now=now,
            )
            session.commit()
            return AgentGovernedPolicy(
                workspace_id=workspace_id,
                agent_id=agent_id,
                **current.model_dump(),
            )

    def get_default_assistant_policy(self, *, workspace_id: str) -> DefaultAssistantPolicy:
        with self._db.session() as session:
            workspace = session.get(WorkspaceOrm, workspace_id)
            if workspace is None:
                raise LookupError("workspace_not_found")
            row = session.get(DefaultAssistantPolicyOrm, workspace_id)
            settings = self._settings(row) if row is not None else GovernedAgentSettings()
            return DefaultAssistantPolicy(workspace_id=workspace_id, **settings.model_dump())

    def replace_default_assistant_policy(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        settings: GovernedAgentSettings,
    ) -> DefaultAssistantPolicy:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            self._lock_workspace(session, workspace_id)
            row = self._ensure_default_policy(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                now=now,
            )
            if row.revision != settings.revision:
                raise AgentPolicyConflictError(row.revision)
            subjects, records, accessible_ids = self._validation_state(
                session, workspace_id=workspace_id, include_default=True
            )
            self._validator.validate_default_assistant_update(
                workspace_id=workspace_id,
                requested=settings,
                subjects=subjects,
                current_policies=records,
                accessible_connector_ids=accessible_ids,
            )
            previous = self._settings(row)
            updated_revision = self._cas_default_policy(
                session,
                row=row,
                settings=settings,
                actor_user_id=actor_user_id,
                now=now,
            )
            current = settings.model_copy(update={"revision": updated_revision})
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type="default_assistant_policy",
                target_id=DEFAULT_ASSISTANT_POLICY_ID,
                previous=previous,
                current=current,
                now=now,
            )
            session.commit()
            return DefaultAssistantPolicy(workspace_id=workspace_id, **current.model_dump())

    @staticmethod
    def _lock_workspace(session: Session, workspace_id: str) -> WorkspaceOrm:
        workspace = session.execute(
            select(WorkspaceOrm).where(WorkspaceOrm.id == workspace_id).with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise LookupError("workspace_not_found")
        return workspace

    @staticmethod
    def _ensure_agent_policy(
        session: Session,
        *,
        agent: AgentProfileOrm,
        actor_user_id: str,
        now: datetime,
    ) -> AgentGovernedPolicyOrm:
        row = session.get(AgentGovernedPolicyOrm, agent.id)
        if row is not None:
            return row
        row = AgentGovernedPolicyOrm(
            agent_id=agent.id,
            workspace_id=agent.workspace_id,
            revision=1,
            policy_json=AgentPolicyRepository._policy_body(GovernedAgentSettings()),
            created_by=actor_user_id,
            updated_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _ensure_default_policy(
        session: Session,
        *,
        workspace_id: str,
        actor_user_id: str,
        now: datetime,
    ) -> DefaultAssistantPolicyOrm:
        row = session.get(DefaultAssistantPolicyOrm, workspace_id)
        if row is not None:
            return row
        row = DefaultAssistantPolicyOrm(
            workspace_id=workspace_id,
            revision=1,
            policy_json=AgentPolicyRepository._policy_body(GovernedAgentSettings()),
            created_by=actor_user_id,
            updated_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def _validation_state(
        self,
        session: Session,
        *,
        workspace_id: str,
        include_default: bool = False,
    ) -> tuple[list[AgentPolicySubject], list[AgentPolicyRecord], set[str]]:
        agents = list(
            session.execute(
                select(AgentProfileOrm).where(
                    AgentProfileOrm.workspace_id == workspace_id,
                    AgentProfileOrm.is_active.is_(True),
                )
            ).scalars()
        )
        connector_aliases = self._accessible_gitlab_connectors(session, workspace_id=workspace_id)
        subjects = [
            AgentPolicySubject(
                agent_id=agent.id,
                workspace_id=workspace_id,
                is_active=agent.is_active,
                allowed_gitlab_connector_ids=frozenset(
                    connector_aliases[name]
                    for name in (agent.allowed_gitlab_connector_names or [])
                    if name in connector_aliases
                ),
            )
            for agent in agents
        ]
        rows = {
            row.agent_id: row
            for row in session.execute(
                select(AgentGovernedPolicyOrm).where(
                    AgentGovernedPolicyOrm.workspace_id == workspace_id
                )
            ).scalars()
        }
        records = [
            AgentPolicyRecord(
                agent_id=agent.id,
                settings=self._settings(rows.get(agent.id)),
            )
            for agent in agents
        ]
        if include_default:
            default_row = session.get(DefaultAssistantPolicyOrm, workspace_id)
            records.append(
                AgentPolicyRecord(
                    agent_id=DEFAULT_ASSISTANT_POLICY_ID,
                    settings=self._settings(default_row),
                )
            )
        return subjects, records, set(connector_aliases.values())

    @staticmethod
    def _accessible_gitlab_connectors(session: Session, *, workspace_id: str) -> dict[str, str]:
        local_rows = session.execute(
            select(GitLabConnectorConfigOrm).where(
                GitLabConnectorConfigOrm.workspace_id == workspace_id,
                GitLabConnectorConfigOrm.is_active.is_(True),
            )
        ).scalars()
        aliases = {row.name: row.id for row in local_rows}
        shared_rows = session.execute(
            select(WorkspaceResourceGrantOrm, GitLabConnectorConfigOrm)
            .join(
                GitLabConnectorConfigOrm,
                GitLabConnectorConfigOrm.id == WorkspaceResourceGrantOrm.resource_id,
            )
            .where(
                WorkspaceResourceGrantOrm.recipient_workspace_id == workspace_id,
                WorkspaceResourceGrantOrm.resource_kind
                == WorkspaceResourceKind.GITLAB_CONNECTOR.value,
                WorkspaceResourceGrantOrm.is_active.is_(True),
                GitLabConnectorConfigOrm.is_active.is_(True),
            )
        ).all()
        for grant, connector in shared_rows:
            aliases.setdefault(grant.alias, connector.id)
        return aliases

    @staticmethod
    def _cas_agent_policy(
        session: Session,
        *,
        row: AgentGovernedPolicyOrm,
        settings: GovernedAgentSettings,
        actor_user_id: str,
        now: datetime,
    ) -> int:
        next_revision = row.revision + 1
        result = cast(
            CursorResult[Any],
            session.execute(
                update(AgentGovernedPolicyOrm)
                .where(
                    AgentGovernedPolicyOrm.agent_id == row.agent_id,
                    AgentGovernedPolicyOrm.workspace_id == row.workspace_id,
                    AgentGovernedPolicyOrm.revision == row.revision,
                )
                .values(
                    revision=next_revision,
                    policy_json=AgentPolicyRepository._policy_body(settings),
                    updated_by=actor_user_id,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            current = session.execute(
                select(AgentGovernedPolicyOrm)
                .where(AgentGovernedPolicyOrm.agent_id == row.agent_id)
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            raise AgentPolicyConflictError(
                current.revision if current is not None else row.revision
            )
        return next_revision

    @staticmethod
    def _cas_default_policy(
        session: Session,
        *,
        row: DefaultAssistantPolicyOrm,
        settings: GovernedAgentSettings,
        actor_user_id: str,
        now: datetime,
    ) -> int:
        next_revision = row.revision + 1
        result = cast(
            CursorResult[Any],
            session.execute(
                update(DefaultAssistantPolicyOrm)
                .where(
                    DefaultAssistantPolicyOrm.workspace_id == row.workspace_id,
                    DefaultAssistantPolicyOrm.revision == row.revision,
                )
                .values(
                    revision=next_revision,
                    policy_json=AgentPolicyRepository._policy_body(settings),
                    updated_by=actor_user_id,
                    updated_at=now,
                )
            ),
        )
        if result.rowcount != 1:
            current = session.execute(
                select(DefaultAssistantPolicyOrm)
                .where(DefaultAssistantPolicyOrm.workspace_id == row.workspace_id)
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            raise AgentPolicyConflictError(
                current.revision if current is not None else row.revision
            )
        return next_revision

    @staticmethod
    def _settings(
        row: AgentGovernedPolicyOrm | DefaultAssistantPolicyOrm | None,
    ) -> GovernedAgentSettings:
        if row is None:
            return GovernedAgentSettings()
        try:
            return GovernedAgentSettings.model_validate({
                **(row.policy_json or {}),
                "revision": row.revision,
            })
        except ValidationError:
            # Corrupt/manual rows must never grant permissions. The existing revision
            # is preserved so an administrator can repair them with a CAS update.
            return GovernedAgentSettings(revision=max(int(row.revision or 1), 1))

    @staticmethod
    def _policy_body(settings: GovernedAgentSettings) -> dict[str, object]:
        return settings.model_dump(mode="json", exclude={"revision"})

    @staticmethod
    def _audit(
        session: Session,
        *,
        workspace_id: str,
        actor_user_id: str,
        target_type: str,
        target_id: str,
        previous: GovernedAgentSettings,
        current: GovernedAgentSettings,
        now: datetime,
    ) -> None:
        session.add(
            WorkspacePolicyAuditEventOrm(
                id=str(uuid4()),
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                action="updated",
                previous_value=previous.model_dump(mode="json"),
                current_value=current.model_dump(mode="json"),
                created_at=now,
            )
        )


__all__ = [
    "AgentPolicyConflictError",
    "AgentPolicyRepository",
    "AgentPolicyValidationError",
]
