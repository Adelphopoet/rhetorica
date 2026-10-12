"""Select shared workspace resources without changing access or delegation grants."""

from rhetorica_backend.contracts.coding import CreateCodingRunRequest
from rhetorica_backend.domain.coding.errors import CodingRunError


class CodingWorkspaceDefaultsService:
    def __init__(self, *, service, repository):
        self.service, self.repository = service, repository

    def view(self, user):
        self.service.require_actor(user, auth_kind="session")
        agents = self.service.agents.list(
            workspace_id=user.workspace_id, limit=100, current_user_id=user.id
        )
        teams = (
            self.service.teams.list(workspace_id=user.workspace_id) if self.service.teams else []
        )
        return {
            "settings": self.repository.get(user.workspace_id),
            "coordinators": [{"id": a.id, "title": a.title} for a in agents],
            "teams": [
                self.service.resources.team_option(user, team)
                for team in teams
                if team.active_revision_id
            ],
        }

    def replace(self, user, request):
        live = self.service.require_actor(user, auth_kind="session", write=True)
        membership = self.service.workspaces.membership(
            workspace_id=user.workspace_id, user_id=user.id
        )
        if not live.is_admin and (membership is None or membership.role != "owner"):
            raise CodingRunError("workspace_owner_access_denied", status_code=403)
        if request.resources:
            if not request.resources.root_agent_id:
                raise CodingRunError("coding_workspace_coordinator_required")
            self.service.validate_resources(
                CreateCodingRunRequest(
                    task="Workspace coding defaults", **request.resources.model_dump()
                ),
                user,
            )
        return self.repository.replace(
            workspace_id=user.workspace_id, actor_user_id=user.id, request=request
        )
