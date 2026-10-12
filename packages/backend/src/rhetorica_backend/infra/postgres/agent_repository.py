from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.agents import (
    AgentProfileItem,
    AgentReactionResponse,
    AgentSettings,
    LiteLlmAgentApiSpikeResult,
    UpsertAgentProfileRequest,
)
from rhetorica_backend.infra.postgres.models import (
    AgentFavoriteOrm,
    AgentProfileOrm,
    AgentStarOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError


class AgentProfileRepository:
    """Persistence gateway for cross-user workspace agents."""

    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    def upsert(
        self,
        request: UpsertAgentProfileRequest,
        *,
        actor_user_id: str | None = None,
    ) -> AgentProfileItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(AgentProfileOrm).where(
                    AgentProfileOrm.workspace_id == request.workspace_id,
                    AgentProfileOrm.slug == request.slug,
                )
            ).scalar_one_or_none()
            if row is None:
                row = self._build_row(
                    request=request,
                    actor_user_id=actor_user_id,
                    now=now,
                )
                session.add(row)
            else:
                self._apply_update(row=row, request=request, actor_user_id=actor_user_id, now=now)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row, current_user_id=actor_user_id)

    def create(
        self,
        request: UpsertAgentProfileRequest,
        *,
        actor_user_id: str | None = None,
    ) -> AgentProfileItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = self._build_row(
                request=request,
                actor_user_id=actor_user_id,
                now=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row, current_user_id=actor_user_id)

    def update(
        self,
        agent_id: str,
        workspace_id: str,
        request: UpsertAgentProfileRequest,
        *,
        actor_user_id: str | None = None,
    ) -> AgentProfileItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(AgentProfileOrm, agent_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            self._apply_update(row=row, request=request, actor_user_id=actor_user_id, now=now)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row, current_user_id=actor_user_id)

    def list(
        self,
        *,
        workspace_id: str,
        current_user_id: str | None = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[AgentProfileItem]:
        with self._db.session() as session:
            star_counts = (
                select(
                    AgentStarOrm.agent_id.label("agent_id"),
                    func.count(AgentStarOrm.user_id).label("star_count"),
                )
                .group_by(AgentStarOrm.agent_id)
                .subquery()
            )
            stmt = (
                select(AgentProfileOrm, func.coalesce(star_counts.c.star_count, 0))
                .outerjoin(star_counts, star_counts.c.agent_id == AgentProfileOrm.id)
                .where(AgentProfileOrm.workspace_id == workspace_id)
            )
            if not include_inactive:
                stmt = stmt.where(AgentProfileOrm.is_active.is_(True))
            stmt = stmt.order_by(
                func.coalesce(star_counts.c.star_count, 0).desc(), AgentProfileOrm.title.asc()
            ).limit(limit)
            rows = session.execute(stmt).all()
            agent_ids = [row.id for row, _count in rows]
            starred = self._reaction_ids(session, AgentStarOrm, agent_ids, current_user_id)
            favorites = self._reaction_ids(session, AgentFavoriteOrm, agent_ids, current_user_id)
            return [
                self._to_contract(
                    row,
                    current_user_id=current_user_id,
                    star_count=int(star_count or 0),
                    is_starred=row.id in starred,
                    is_favorite=row.id in favorites,
                )
                for row, star_count in rows
            ]

    def get(
        self,
        *,
        agent_id: str,
        workspace_id: str,
        current_user_id: str | None = None,
        active_only: bool = True,
    ) -> AgentProfileItem | None:
        with self._db.session() as session:
            row = session.get(AgentProfileOrm, agent_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            if active_only and not row.is_active:
                return None
            return self._to_contract(
                row,
                current_user_id=current_user_id,
                star_count=self._star_count(session, agent_id),
                is_starred=self._has_reaction(session, AgentStarOrm, agent_id, current_user_id),
                is_favorite=self._has_reaction(
                    session, AgentFavoriteOrm, agent_id, current_user_id
                ),
            )

    def delete(self, *, agent_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(AgentProfileOrm, agent_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            session.delete(row)
            session.commit()
            return True

    def set_star(
        self,
        *,
        agent_id: str,
        workspace_id: str,
        user_id: str,
        enabled: bool,
    ) -> AgentReactionResponse | None:
        return self._set_reaction(
            model=AgentStarOrm,
            agent_id=agent_id,
            workspace_id=workspace_id,
            user_id=user_id,
            enabled=enabled,
        )

    def set_favorite(
        self,
        *,
        agent_id: str,
        workspace_id: str,
        user_id: str,
        enabled: bool,
    ) -> AgentReactionResponse | None:
        return self._set_reaction(
            model=AgentFavoriteOrm,
            agent_id=agent_id,
            workspace_id=workspace_id,
            user_id=user_id,
            enabled=enabled,
        )

    @staticmethod
    def litellm_agent_api_spike() -> LiteLlmAgentApiSpikeResult:
        return LiteLlmAgentApiSpikeResult(
            source="LiteLLM OpenAPI 1.83.14",
            supports_agent_catalog=True,
            supported_paths=[
                "/v1/agents",
                "/v1/agents/{agent_id}",
                "/public/agent_hub",
                "/public/agents/fields",
            ],
            notes=[
                "LiteLLM agents are A2A/AI Hub catalog objects, not Rhetorica runtime profiles.",
                "The API shape does not cover Rhetorica connector allowlists, stars, favorites, or workspace RBAC.",
                "Keep LiteLLM as the LLM gateway in v1; add import/sync later if needed.",
            ],
            raw_schema_summary={
                "create_schema": "AgentConfig(agent_name, agent_card_params, litellm_params)",
                "response_schema": "AgentResponse(agent_id, agent_name, agent_card_params)",
            },
        )

    @staticmethod
    def _build_row(
        *,
        request: UpsertAgentProfileRequest,
        actor_user_id: str | None,
        now: datetime,
    ) -> AgentProfileOrm:
        return AgentProfileOrm(
            id=str(uuid4()),
            workspace_id=request.workspace_id,
            slug=request.slug,
            title=request.title,
            description=request.description,
            admin_prompt=request.admin_prompt,
            is_active=request.is_active,
            llm_connector_name=request.llm_connector_name,
            allowed_database_connector_names=request.allowed_database_connector_names,
            allowed_rag_connector_names=request.allowed_rag_connector_names,
            allowed_documentation_connector_names=request.allowed_documentation_connector_names,
            allowed_gitlab_connector_names=request.allowed_gitlab_connector_names,
            allowed_mcp_connector_names=request.allowed_mcp_connector_names,
            allowed_airflow_connector_names=request.allowed_airflow_connector_names,
            allowed_kubernetes_connector_names=request.allowed_kubernetes_connector_names,
            allowed_victoriametrics_connector_names=(
                request.allowed_victoriametrics_connector_names
            ),
            allowed_workspace_skill_ids=request.allowed_workspace_skill_ids,
            settings=request.settings.model_dump(mode="json"),
            created_by=actor_user_id,
            updated_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _apply_update(
        *,
        row: AgentProfileOrm,
        request: UpsertAgentProfileRequest,
        actor_user_id: str | None,
        now: datetime,
    ) -> None:
        row.slug = request.slug
        row.title = request.title
        row.description = request.description
        row.admin_prompt = request.admin_prompt
        row.is_active = request.is_active
        row.llm_connector_name = request.llm_connector_name
        row.allowed_database_connector_names = request.allowed_database_connector_names
        row.allowed_rag_connector_names = request.allowed_rag_connector_names
        row.allowed_documentation_connector_names = request.allowed_documentation_connector_names
        row.allowed_gitlab_connector_names = request.allowed_gitlab_connector_names
        row.allowed_mcp_connector_names = request.allowed_mcp_connector_names
        row.allowed_airflow_connector_names = request.allowed_airflow_connector_names
        row.allowed_kubernetes_connector_names = request.allowed_kubernetes_connector_names
        row.allowed_victoriametrics_connector_names = (
            request.allowed_victoriametrics_connector_names
        )
        row.allowed_workspace_skill_ids = request.allowed_workspace_skill_ids
        row.settings = request.settings.model_dump(mode="json")
        row.updated_by = actor_user_id
        row.updated_at = now

    def _set_reaction(
        self,
        *,
        model: type[AgentStarOrm] | type[AgentFavoriteOrm],
        agent_id: str,
        workspace_id: str,
        user_id: str,
        enabled: bool,
    ) -> AgentReactionResponse | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            agent = session.get(AgentProfileOrm, agent_id)
            if agent is None or agent.workspace_id != workspace_id or not agent.is_active:
                return None
            existing = session.execute(
                select(model).where(model.agent_id == agent_id, model.user_id == user_id)
            ).scalar_one_or_none()
            if enabled and existing is None:
                session.add(model(agent_id=agent_id, user_id=user_id, created_at=now))
            if not enabled and existing is not None:
                session.execute(
                    delete(model).where(model.agent_id == agent_id, model.user_id == user_id)
                )
            session.commit()
            return AgentReactionResponse(
                agent_id=agent_id,
                starred=enabled if model is AgentStarOrm else None,
                favorite=enabled if model is AgentFavoriteOrm else None,
                star_count=self._star_count(session, agent_id),
            )

    @staticmethod
    def _reaction_ids(
        session,
        model: type[AgentStarOrm] | type[AgentFavoriteOrm],
        agent_ids: list[str],
        current_user_id: str | None,
    ) -> set[str]:
        if not current_user_id or not agent_ids:
            return set()
        return set(
            session.execute(
                select(model.agent_id).where(
                    model.user_id == current_user_id,
                    model.agent_id.in_(agent_ids),
                )
            ).scalars()
        )

    @staticmethod
    def _has_reaction(
        session,
        model: type[AgentStarOrm] | type[AgentFavoriteOrm],
        agent_id: str,
        current_user_id: str | None,
    ) -> bool:
        if not current_user_id:
            return False
        return (
            session.execute(
                select(model).where(model.agent_id == agent_id, model.user_id == current_user_id)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def _star_count(session, agent_id: str) -> int:
        return int(
            session.execute(
                select(func.count(AgentStarOrm.user_id)).where(AgentStarOrm.agent_id == agent_id)
            ).scalar_one()
            or 0
        )

    @staticmethod
    def _to_contract(
        row: AgentProfileOrm,
        *,
        current_user_id: str | None = None,
        star_count: int | None = None,
        is_starred: bool = False,
        is_favorite: bool = False,
    ) -> AgentProfileItem:
        _ = current_user_id
        return AgentProfileItem(
            id=row.id,
            workspace_id=row.workspace_id,
            slug=row.slug,
            title=row.title,
            description=row.description,
            admin_prompt=row.admin_prompt,
            is_active=row.is_active,
            llm_connector_name=row.llm_connector_name,
            allowed_database_connector_names=row.allowed_database_connector_names or [],
            allowed_rag_connector_names=row.allowed_rag_connector_names or [],
            allowed_documentation_connector_names=(
                getattr(row, "allowed_documentation_connector_names", None) or []
            ),
            allowed_gitlab_connector_names=(
                getattr(row, "allowed_gitlab_connector_names", None) or []
            ),
            allowed_mcp_connector_names=getattr(row, "allowed_mcp_connector_names", None) or [],
            allowed_airflow_connector_names=(
                getattr(row, "allowed_airflow_connector_names", None) or []
            ),
            allowed_kubernetes_connector_names=(
                getattr(row, "allowed_kubernetes_connector_names", None) or []
            ),
            allowed_victoriametrics_connector_names=(
                getattr(row, "allowed_victoriametrics_connector_names", None) or []
            ),
            allowed_workspace_skill_ids=getattr(row, "allowed_workspace_skill_ids", None) or [],
            settings=AgentSettings.model_validate(row.settings or {}),
            star_count=star_count or 0,
            is_starred=is_starred,
            is_favorite=is_favorite,
            created_by=row.created_by,
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
