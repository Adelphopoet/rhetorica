"""Live resource and channel policy, independent of coding admission persistence."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.coding import (
    CodingAgentTeamMember,
    CodingAgentTeamSnapshot,
    CodingRun,
    CreateCodingRunRequest,
)
from rhetorica_backend.domain.coding.errors import CodingRunError, CodingTeamValidationError
from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID
from rhetorica_backend.domain.coding.teams import (
    legacy_projection,
    legacy_team_snapshot,
    run_team_snapshot,
)
from rhetorica_backend.infra.postgres.agent_policy_repository import AgentPolicyRepository
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_environment_repository import (
    CodingEnvironmentRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.gitlab_connector_repository import GitLabConnectorRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository


class CodingResourcePolicy:
    def __init__(
        self,
        *,
        users: UserAccountRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
        policies: AgentPolicyRepository,
        gitlab_connectors: GitLabConnectorRepository,
        databases: ConnectorRepository,
        environments: CodingEnvironmentRepository,
        enabled,
        launch_defaults: dict[str, CreateCodingRunRequest] | None = None,
        workspace_defaults=None,
        teams=None,
    ):
        self.teams = teams
        self.users = users
        self.workspaces = workspaces
        self.agents = agents
        self.policies = policies
        self.gitlab_connectors = gitlab_connectors
        self.databases = databases
        self.environments = environments
        self.enabled = enabled
        self.launch_defaults = launch_defaults or {}
        self.workspace_defaults = workspace_defaults

    def require_actor(self, user, *, auth_kind: str, write: bool = False):
        if auth_kind != "session":
            raise CodingRunError("channel_not_allowed", status_code=403)
        live = self.users.get_by_id(user_id=user.id)
        membership = self.workspaces.membership(workspace_id=user.workspace_id, user_id=user.id)
        if (
            not live
            or not live.is_active
            or membership is None
            or (write and membership.role == "viewer")
        ):
            raise CodingRunError("workspace_access_denied", status_code=403)
        return live.for_workspace(user.workspace_id)

    def root_policy(self, user, root_agent_id):
        if not root_agent_id or root_agent_id == DEFAULT_ASSISTANT_POLICY_ID:
            return self.policies.get_default_assistant_policy(workspace_id=user.workspace_id)
        self.agent(user, root_agent_id)
        policy = self.policies.get_agent_policy(
            workspace_id=user.workspace_id, agent_id=root_agent_id
        )
        if policy is None:
            raise CodingRunError("coding_root_policy_required", status_code=403)
        return policy

    def agent(self, user, agent_id):
        profile = self.agents.get(
            agent_id=agent_id,
            workspace_id=user.workspace_id,
            current_user_id=user.id,
            active_only=True,
        )
        if profile is None:
            raise CodingRunError("coding_agent_unavailable", status_code=403)
        return profile

    def resolve_team(self, request: CreateCodingRunRequest, user: Any) -> CodingAgentTeamSnapshot:
        """Return the immutable team a new run would admit, or a legacy projection."""

        coordinator_id = request.root_agent_id or DEFAULT_ASSISTANT_POLICY_ID
        if request.coding_team_id:
            if self.teams is None:
                raise CodingRunError("coding_teams_unavailable", status_code=503)
            team: CodingAgentTeamSnapshot = self.teams.snapshot(
                workspace_id=user.workspace_id, team_id=request.coding_team_id
            )
            if team.coordinator_agent_profile_id != coordinator_id:
                raise CodingTeamValidationError("coding_team_coordinator_mismatch")
            return team
        if request.developer_agent_id is None or request.qa_agent_id is None:
            raise CodingRunError("coding_team_required")
        if request.developer_agent_id == request.qa_agent_id:
            raise CodingRunError("independent_qa_agent_required")
        try:
            return legacy_team_snapshot(
                coordinator_agent_profile_id=coordinator_id,
                developer_agent_id=request.developer_agent_id,
                qa_agent_id=request.qa_agent_id,
            )
        except ValueError:
            raise CodingRunError("coding_team_coordinator_member_duplicate") from None

    def validate_resources(self, request, user):
        root, _team, children, environment = self.admit_resources(request, user)
        return root, children, environment

    def admit_resources(
        self,
        request: CreateCodingRunRequest,
        user: Any,
        *,
        team: CodingAgentTeamSnapshot | None = None,
    ) -> tuple[Any, CodingAgentTeamSnapshot, list[Any], Any]:
        """Repeat live grants for every member; the team itself grants nothing."""

        root = self.root_policy(user, request.root_agent_id)
        team = team or self.resolve_team(request, user)

        def fail(code: str, member: CodingAgentTeamMember) -> CodingTeamValidationError:
            return CodingTeamValidationError(
                code, member_id=member.id, member_label=member.label, status_code=403
            )

        children = []
        for member in team.members:
            try:
                children.append(self.agent(user, member.agent_profile_id))
            except CodingRunError:
                raise fail("coding_agent_unavailable", member) from None
            if member.agent_profile_id not in root.allowed_subagent_ids:
                raise fail("coding_delegation_not_allowed", member)
        connectors = {
            c.id: c
            for c in self.gitlab_connectors.list(user.workspace_id, limit=100)
            if c.is_active
        }
        connector = connectors.get(request.gitlab_connector_id)
        if connector is None:
            raise CodingRunError("coding_gitlab_access_denied", status_code=403)
        for member, child in zip(team.members, children, strict=True):
            if connector.name not in child.allowed_gitlab_connector_names:
                raise fail("coding_gitlab_access_denied", member)
        if (
            request.root_agent_id
            and request.root_agent_id != DEFAULT_ASSISTANT_POLICY_ID
            and connector.name
            not in self.agent(user, request.root_agent_id).allowed_gitlab_connector_names
        ):
            raise CodingRunError("coding_gitlab_access_denied", status_code=403)
        if request.database_connector_id:
            databases = {
                c.id: c for c in self.databases.list(user.workspace_id, limit=100) if c.is_active
            }
            database = databases.get(request.database_connector_id)
            if database is None:
                raise CodingRunError("coding_database_access_denied", status_code=403)
            for member, child in zip(team.members, children, strict=True):
                if (
                    database.name not in child.allowed_database_connector_names
                    or not child.settings.run_sql_allowed
                ):
                    raise fail("coding_database_access_denied", member)
            if request.root_agent_id and request.root_agent_id != DEFAULT_ASSISTANT_POLICY_ID:
                parent = self.agent(user, request.root_agent_id)
                if (
                    database.name not in parent.allowed_database_connector_names
                    or not parent.settings.run_sql_allowed
                ):
                    raise CodingRunError("coding_database_access_denied", status_code=403)
            if database.connection.engine != "trino":
                raise CodingRunError("dbt_gateway_connector_must_be_trino")
        environment = self.environments.get(profile_id=request.environment_profile_id)
        if environment is None:
            raise CodingRunError("coding_environment_required")
        return root, team, children, environment

    def validate_run(self, run: CodingRun, user) -> None:
        """Live authorization for the admitted snapshot; a newer team never applies."""

        team = run_team_snapshot(run)
        self.admit_resources(
            CreateCodingRunRequest(
                task="Live coding authorization",
                root_agent_id=run.root_agent_id,
                developer_agent_id=run.developer_agent_id,
                qa_agent_id=run.qa_agent_id,
                gitlab_connector_id=run.repository.gitlab_connector_id,
                project_id=run.repository.project_id,
                target_branch=run.repository.target_branch,
                environment_profile_id=run.environment_profile_id,
                database_connector_id=run.data_warehouse.connector_id
                if run.data_warehouse
                else None,
            ),
            user,
            team=team,
        )

    def capabilities(
        self, user, *, auth_kind="session", root_agent_id=None, use_workspace_defaults=True
    ):
        self.require_actor(user, auth_kind=auth_kind)
        stored = self.workspace_defaults.get(user.workspace_id) if self.workspace_defaults else None
        defaults = stored.resources if stored else self.launch_defaults.get(root_agent_id)
        if root_agent_id is None and defaults is not None and use_workspace_defaults:
            root_agent_id = defaults.root_agent_id
        root = self.root_policy(user, root_agent_id)
        agents = []
        for agent_id in root.allowed_subagent_ids:
            try:
                agents.append(self.agent(user, agent_id))
            except CodingRunError:
                continue
        defaults_error = None
        defaults_member = None
        defaults_team = None
        teams = self._available_teams(user, root_agent_id, agents)
        own = [team["id"] for team in teams if team["available"]]
        if defaults is not None and own and defaults.coding_team_id not in own and len(own) == 1:
            # The selected coordinator's only available team is the unambiguous default.
            defaults = defaults.model_copy(update={"coding_team_id": own[0]})
        if defaults is not None:
            defaults = defaults.model_copy(update={"root_agent_id": root_agent_id})
            try:
                request = CreateCodingRunRequest(
                    task="Coding defaults",
                    **defaults.model_dump(exclude={"task", "conversation_id"}),
                )
                _, defaults_team, _, _ = self.admit_resources(request, user)
            except CodingRunError as exc:
                defaults_error = exc.code
                defaults_member = getattr(exc, "member_label", None)
                defaults = None
        if defaults is not None and defaults_team is not None:
            # Compatibility projection for clients that still read developer/QA IDs.
            developer_id, qa_id = legacy_projection(defaults_team)
            defaults = defaults.model_copy(
                update={"developer_agent_id": developer_id, "qa_agent_id": qa_id}
            )
        membership = self.workspaces.membership(workspace_id=user.workspace_id, user_id=user.id)
        connectors = [c for c in self.gitlab_connectors.list(user.workspace_id, 100) if c.is_active]
        environments = self.environments.list_latest()
        root_connectors = (
            self.agent(user, root_agent_id).allowed_gitlab_connector_names
            if root_agent_id and root_agent_id != DEFAULT_ASSISTANT_POLICY_ID
            else [c.name for c in connectors]
        )
        eligible = any(
            c.name in root_connectors
            and all(c.name in agent.allowed_gitlab_connector_names for agent in team["agents"])
            for team in teams
            if team["available"]
            for c in connectors
        )
        return {
            "can_start_coding": bool(
                self.enabled and membership.role != "viewer" and eligible and environments
            ),
            "root_agent_id": root_agent_id,
            "workspace_defaults_revision": stored.revision if stored else 0,
            "defaults_unavailable_reason": defaults_error,
            "defaults_unavailable_member": defaults_member,
            "launch_defaults": defaults.model_dump(exclude={"task", "conversation_id"})
            if defaults
            else None,
            "team": defaults_team.model_dump(mode="json") if defaults_team else None,
            "teams": [
                {key: value for key, value in team.items() if key != "agents"} for team in teams
            ],
            "enabled": self.enabled,
            "specialists": [
                {
                    "id": a.id,
                    "title": a.title,
                    "gitlab_connectors": a.allowed_gitlab_connector_names,
                    "database_connectors": a.allowed_database_connector_names,
                }
                for a in agents
            ],
            "gitlab_connectors": [{"id": c.id, "name": c.name} for c in connectors],
            "database_connectors": [
                {"id": c.id, "name": c.name}
                for c in self.databases.list(user.workspace_id, 100)
                if c.is_active and c.connection.engine == "trino"
            ],
            "environments": [{"id": e.id, "revision": e.revision} for e in environments],
        }

    def team_options(self, user: Any, *, auth_kind: str = "session") -> list[dict[str, Any]]:
        """Active teams a member may pick in chat; selecting one picks its coordinator."""

        self.require_actor(user, auth_kind=auth_kind)
        if self.teams is None:
            return []
        return [
            self.team_option(user, team)
            for team in self.teams.list(workspace_id=user.workspace_id)
            if team.active_revision_id
        ]

    def team_option(self, user: Any, team: Any) -> dict[str, Any]:
        option = {"id": team.id, "name": team.name, "active_revision_id": team.active_revision_id}
        try:
            snapshot: CodingAgentTeamSnapshot = self.teams.snapshot(
                workspace_id=user.workspace_id, team_id=team.id
            )
            if snapshot.coordinator_agent_profile_id != DEFAULT_ASSISTANT_POLICY_ID:
                # Choosing the team in chat starts a conversation with its coordinator.
                self.agent(user, snapshot.coordinator_agent_profile_id)
        except CodingRunError as exc:
            return {
                **option,
                "available": False,
                "unavailable_reason": exc.code,
                "unavailable_member": getattr(exc, "member_label", None),
            }
        return {
            **option,
            "available": True,
            "revision": snapshot.revision,
            "coordinator_agent_profile_id": snapshot.coordinator_agent_profile_id,
            "members": [
                {
                    "id": m.id,
                    "label": m.label,
                    "capability": m.capability,
                    "agent_profile_id": m.agent_profile_id,
                }
                for m in snapshot.members
            ],
        }

    def _available_teams(
        self, user: Any, root_agent_id: str | None, agents: list[Any]
    ) -> list[dict[str, Any]]:
        """Active teams coordinated by the selected root, with an unavailable reason."""

        if self.teams is None:
            return []
        coordinator_id = root_agent_id or DEFAULT_ASSISTANT_POLICY_ID
        allowed = {agent.id: agent for agent in agents}
        result: list[dict[str, Any]] = []
        for team in self.teams.list(workspace_id=user.workspace_id):
            if team.active_revision_id is None:
                continue
            try:
                snapshot = self.teams.snapshot(workspace_id=user.workspace_id, team_id=team.id)
            except CodingRunError as exc:
                result.append({
                    "id": team.id,
                    "name": team.name,
                    "available": False,
                    "unavailable_reason": exc.code,
                    "unavailable_member": getattr(exc, "member_label", None),
                    "agents": [],
                })
                continue
            if snapshot.coordinator_agent_profile_id != coordinator_id:
                continue
            missing = next((m for m in snapshot.members if m.agent_profile_id not in allowed), None)
            result.append({
                "id": team.id,
                "name": team.name,
                "revision_id": snapshot.revision_id,
                "revision": snapshot.revision,
                "available": missing is None,
                "unavailable_reason": None if missing is None else "coding_delegation_not_allowed",
                "unavailable_member": None if missing is None else missing.label,
                "members": [
                    {
                        "id": m.id,
                        "label": m.label,
                        "capability": m.capability,
                        "agent_profile_id": m.agent_profile_id,
                    }
                    for m in snapshot.members
                ],
                "agents": [allowed[m.agent_profile_id] for m in snapshot.members]
                if missing is None
                else [],
            })
        return result
