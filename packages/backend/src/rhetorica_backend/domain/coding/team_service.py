from datetime import UTC, datetime
from typing import Any

from rhetorica_backend.contracts.coding import (
    CodingAgentTeam,
    CodingAgentTeamDetail,
    CodingAgentTeamRevision,
    UpsertCodingAgentTeamRevision,
)
from rhetorica_backend.domain.coding.errors import CodingRunError


class CodingAgentTeamService:
    def __init__(self, *, coding_service: Any, repository: Any, validator: Any) -> None:
        self._coding, self._repository, self._validator = coding_service, repository, validator

    def list(self, user: Any) -> list[CodingAgentTeamDetail]:
        self._require_admin(user, write=False)
        return [
            self._repository.detail(workspace_id=user.workspace_id, team_id=team.id)
            for team in self._repository.list(workspace_id=user.workspace_id)
        ]

    def detail(self, user: Any, *, team_id: str) -> CodingAgentTeamDetail:
        self._require_admin(user, write=False)
        detail: CodingAgentTeamDetail = self._repository.detail(
            workspace_id=user.workspace_id, team_id=team_id
        )
        return detail

    def create_revision(
        self, user: Any, request: UpsertCodingAgentTeamRevision
    ) -> CodingAgentTeamRevision:
        self._require_admin(user)
        # An invalid draft is never persisted as a selectable revision.
        self._validator.validate(
            revision=CodingAgentTeamRevision(
                id="pending",
                workspace_id=user.workspace_id,
                team_id=request.team_id or "pending",
                revision=request.expected_revision + 1,
                coordinator_agent_profile_id=request.coordinator_agent_profile_id,
                coordinator_instruction=request.coordinator_instruction,
                members=request.members,
                created_at=datetime.now(UTC),
                created_by=user.id,
            )
        )
        revision: CodingAgentTeamRevision = self._repository.append_revision(
            workspace_id=user.workspace_id,
            team_id=request.team_id,
            name=request.name,
            actor_user_id=user.id,
            request=request,
        )
        return revision

    def activate(self, user: Any, *, team_id: str, revision_id: str) -> CodingAgentTeam:
        self._require_admin(user)
        revision = self._repository.get_revision(
            workspace_id=user.workspace_id, revision_id=revision_id
        )
        if revision.team_id != team_id:
            raise CodingRunError("coding_team_revision_not_found", status_code=404)
        # A revoked grant leaves the previously active revision in place.
        self._validator.validate(revision=revision)
        team: CodingAgentTeam = self._repository.activate(
            workspace_id=user.workspace_id, team_id=team_id, revision_id=revision_id
        )
        return team

    def _require_admin(self, user: Any, *, write: bool = True) -> Any:
        live = self._coding.require_actor(user, auth_kind="session", write=write)
        if not live.is_admin:
            raise CodingRunError("admin_access_required", status_code=403)
        return live
