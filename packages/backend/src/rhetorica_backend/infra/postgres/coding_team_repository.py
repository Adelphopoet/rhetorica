"""Atomic revision storage for administrator-configured coding teams."""

from datetime import UTC, datetime
from uuid import uuid4

from rhetorica_backend.contracts.coding import (
    CodingAgentTeam,
    CodingAgentTeamDetail,
    CodingAgentTeamRevision,
    CreateCodingAgentTeamRevision,
)
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.coding_team_models import (
    CodingAgentTeamOrm,
    CodingAgentTeamRevisionOrm,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class CodingAgentTeamRepository:
    def __init__(self, db: DatabaseRuntime) -> None:
        self._db = db

    def list(self, *, workspace_id: str) -> list[CodingAgentTeam]:
        with self._db.session() as session:
            rows = session.scalars(
                select(CodingAgentTeamOrm)
                .where(CodingAgentTeamOrm.workspace_id == workspace_id)
                .order_by(CodingAgentTeamOrm.name)
            ).all()
            return [self._team(row) for row in rows]

    def get_team(self, *, workspace_id: str, team_id: str) -> CodingAgentTeam:
        with self._db.session() as session:
            row = self._owned_team(session, workspace_id, team_id)
            return self._team(row)

    def detail(self, *, workspace_id: str, team_id: str) -> CodingAgentTeamDetail:
        with self._db.session() as session:
            team = self._owned_team(session, workspace_id, team_id)
            rows = session.scalars(
                select(CodingAgentTeamRevisionOrm)
                .where(
                    CodingAgentTeamRevisionOrm.workspace_id == workspace_id,
                    CodingAgentTeamRevisionOrm.team_id == team_id,
                )
                .order_by(CodingAgentTeamRevisionOrm.revision.desc())
            ).all()
            return CodingAgentTeamDetail(
                team=self._team(team), revisions=[self._revision(row) for row in rows]
            )

    def get_revision(self, *, workspace_id: str, revision_id: str) -> CodingAgentTeamRevision:
        with self._db.session() as session:
            row = session.scalar(
                select(CodingAgentTeamRevisionOrm).where(
                    CodingAgentTeamRevisionOrm.workspace_id == workspace_id,
                    CodingAgentTeamRevisionOrm.id == revision_id,
                )
            )
            if row is None:
                raise CodingRunError("coding_team_revision_not_found", status_code=404)
            return self._revision(row)

    def active_revision(
        self, *, workspace_id: str, team_id: str
    ) -> tuple[CodingAgentTeam, CodingAgentTeamRevision]:
        """Resolve the revision a new run must snapshot, never a draft revision."""

        with self._db.session() as session:
            team = self._owned_team(session, workspace_id, team_id)
            if team.active_revision_id is None:
                raise CodingRunError("coding_team_inactive", status_code=422)
            row = session.scalar(
                select(CodingAgentTeamRevisionOrm).where(
                    CodingAgentTeamRevisionOrm.id == team.active_revision_id,
                    CodingAgentTeamRevisionOrm.team_id == team.id,
                    CodingAgentTeamRevisionOrm.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise CodingRunError("coding_team_inactive", status_code=422)
            return self._team(team), self._revision(row)

    def append_revision(
        self,
        *,
        workspace_id: str,
        team_id: str | None,
        name: str,
        actor_user_id: str,
        request: CreateCodingAgentTeamRevision,
    ) -> CodingAgentTeamRevision:
        try:
            with self._db.session() as session:
                if team_id is not None:
                    team = session.scalar(
                        select(CodingAgentTeamOrm)
                        .where(
                            CodingAgentTeamOrm.id == team_id,
                            CodingAgentTeamOrm.workspace_id == workspace_id,
                        )
                        .with_for_update()
                    )
                    if team is None:
                        raise CodingRunError("coding_team_not_found", status_code=404)
                else:
                    team = CodingAgentTeamOrm(
                        id=str(uuid4()), workspace_id=workspace_id, name=name, latest_revision=0
                    )
                    session.add(team)
                    session.flush()
                if team.latest_revision != request.expected_revision:
                    raise CodingRunError("coding_team_revision_conflict", status_code=409)
                now = datetime.now(UTC)
                row = CodingAgentTeamRevisionOrm(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    team_id=team.id,
                    revision=team.latest_revision + 1,
                    coordinator_agent_profile_id=request.coordinator_agent_profile_id,
                    coordinator_instruction=request.coordinator_instruction,
                    members_json=[member.model_dump(mode="json") for member in request.members],
                    is_active=False,
                    created_by=actor_user_id,
                    created_at=now,
                )
                team.name = name
                team.latest_revision, team.updated_at = row.revision, now
                session.add(row)
                session.commit()
                return self._revision(row)
        except IntegrityError:
            # Only the workspace-unique name can conflict; revisions are serialized above.
            raise CodingRunError("coding_team_name_conflict", status_code=409) from None

    def activate(self, *, workspace_id: str, team_id: str, revision_id: str) -> CodingAgentTeam:
        with self._db.session() as session:
            team = session.scalar(
                select(CodingAgentTeamOrm)
                .where(
                    CodingAgentTeamOrm.id == team_id,
                    CodingAgentTeamOrm.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            revision = session.scalar(
                select(CodingAgentTeamRevisionOrm).where(
                    CodingAgentTeamRevisionOrm.id == revision_id,
                    CodingAgentTeamRevisionOrm.team_id == team_id,
                    CodingAgentTeamRevisionOrm.workspace_id == workspace_id,
                )
            )
            if team is None or revision is None:
                raise CodingRunError("coding_team_revision_not_found", status_code=404)
            session.execute(
                update(CodingAgentTeamRevisionOrm)
                .where(CodingAgentTeamRevisionOrm.team_id == team_id)
                .values(is_active=False)
            )
            team.active_revision_id, team.updated_at = revision.id, datetime.now(UTC)
            revision.is_active = True
            session.commit()
            return self._team(team)

    @staticmethod
    def _owned_team(session: Session, workspace_id: str, team_id: str) -> CodingAgentTeamOrm:
        row = session.scalar(
            select(CodingAgentTeamOrm).where(
                CodingAgentTeamOrm.id == team_id,
                CodingAgentTeamOrm.workspace_id == workspace_id,
            )
        )
        if row is None:
            raise CodingRunError("coding_team_not_found", status_code=404)
        return row

    @staticmethod
    def _team(row: CodingAgentTeamOrm) -> CodingAgentTeam:
        return CodingAgentTeam.model_validate(row, from_attributes=True)

    @staticmethod
    def _revision(row: CodingAgentTeamRevisionOrm) -> CodingAgentTeamRevision:
        return CodingAgentTeamRevision.model_validate({
            "id": row.id,
            "workspace_id": row.workspace_id,
            "team_id": row.team_id,
            "revision": row.revision,
            "coordinator_agent_profile_id": row.coordinator_agent_profile_id,
            "coordinator_instruction": row.coordinator_instruction,
            "members": row.members_json,
            "is_active": row.is_active,
            "created_by": row.created_by,
            "created_at": row.created_at,
        })
