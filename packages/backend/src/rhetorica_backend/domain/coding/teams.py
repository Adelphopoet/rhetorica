"""Admission-time validation for a configured coding-agent team."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.coding import (
    CodingAgentTeam,
    CodingAgentTeamMember,
    CodingAgentTeamRevision,
    CodingAgentTeamSnapshot,
    CodingTeamMemberCapability,
)
from rhetorica_backend.domain.coding.errors import CodingTeamValidationError
from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID

LEGACY_TEAM_ID = "legacy-developer-qa"


class CodingAgentTeamValidator:
    """Checks references only; a team never creates policy or skill grants."""

    def __init__(
        self, *, agents: Any, policies: Any, skills: Any, gitlab_connectors: Any = None
    ) -> None:
        self._agents = agents
        self._policies = policies
        self._skills = skills
        self._gitlab_connectors = gitlab_connectors

    def validate(self, *, revision: CodingAgentTeamRevision) -> None:
        workspace_id = revision.workspace_id
        coordinator_id = revision.coordinator_agent_profile_id
        coordinator = None
        if coordinator_id == DEFAULT_ASSISTANT_POLICY_ID:
            policy = self._policies.get_default_assistant_policy(workspace_id=workspace_id)
        else:
            coordinator = self._agents.get(
                agent_id=coordinator_id, workspace_id=workspace_id, active_only=True
            )
            if coordinator is None:
                raise CodingTeamValidationError("coding_team_coordinator_unavailable")
            policy = self._policies.get_agent_policy(
                workspace_id=workspace_id, agent_id=coordinator_id
            )
        if policy is None:
            raise CodingTeamValidationError("coding_team_coordinator_unavailable")
        allowed = set(policy.allowed_subagent_ids)
        # A run needs one GitLab connector shared by the coordinator and every member.
        shared = self._active_gitlab_connector_names(workspace_id)
        if coordinator is not None and shared is not None:
            shared &= set(coordinator.allowed_gitlab_connector_names)
        for member in revision.members:
            agent = self._validate_member(member, workspace_id, allowed)
            if shared is not None:
                shared &= set(agent.allowed_gitlab_connector_names)
                if not shared:
                    raise CodingTeamValidationError(
                        "coding_team_member_gitlab_grant_missing",
                        member_id=member.id,
                        member_label=member.label,
                    )

    def _validate_member(
        self, member: CodingAgentTeamMember, workspace_id: str, allowed: set[str]
    ) -> Any:
        def fail(code: str) -> CodingTeamValidationError:
            return CodingTeamValidationError(code, member_id=member.id, member_label=member.label)

        agent = self._agents.get(
            agent_id=member.agent_profile_id, workspace_id=workspace_id, active_only=True
        )
        if agent is None:
            raise fail("coding_team_member_unavailable")
        if member.agent_profile_id not in allowed:
            raise fail("coding_team_delegation_not_allowed")
        for binding in member.skill_version_bindings:
            if binding.skill_id not in agent.allowed_workspace_skill_ids:
                raise fail("coding_team_skill_not_allowed")
            if (
                self._skills.get_version(
                    skill_id=binding.skill_id,
                    workspace_id=workspace_id,
                    version_digest=binding.version_digest,
                )
                is None
            ):
                raise fail("coding_team_skill_version_unavailable")
        return agent

    def _active_gitlab_connector_names(self, workspace_id: str) -> set[str] | None:
        if self._gitlab_connectors is None:
            return None
        return {
            connector.name
            for connector in self._gitlab_connectors.list(workspace_id, limit=100)
            if connector.is_active
        }


def team_snapshot(revision: CodingAgentTeamRevision, *, team_name: str) -> CodingAgentTeamSnapshot:
    return CodingAgentTeamSnapshot(
        team_id=revision.team_id,
        team_name=team_name,
        revision_id=revision.id,
        revision=revision.revision,
        coordinator_agent_profile_id=revision.coordinator_agent_profile_id,
        coordinator_instruction=revision.coordinator_instruction,
        members=revision.members,
    )


def legacy_team_snapshot(
    *, coordinator_agent_profile_id: str, developer_agent_id: str, qa_agent_id: str
) -> CodingAgentTeamSnapshot:
    """Project the legacy developer/QA selection onto the same team runtime."""

    return CodingAgentTeamSnapshot(
        team_id=LEGACY_TEAM_ID,
        team_name="Developer and QA",
        revision_id=LEGACY_TEAM_ID,
        revision=1,
        coordinator_agent_profile_id=coordinator_agent_profile_id,
        members=[
            CodingAgentTeamMember(
                id="developer",
                agent_profile_id=developer_agent_id,
                label="Developer",
                capability=CodingTeamMemberCapability.CANDIDATE,
            ),
            CodingAgentTeamMember(
                id="qa",
                agent_profile_id=qa_agent_id,
                label="QA",
                capability=CodingTeamMemberCapability.REVIEW,
            ),
        ],
        legacy=True,
    )


def run_team_snapshot(run: Any) -> CodingAgentTeamSnapshot:
    """The admitted team of a run; runs admitted before teams use the legacy projection."""

    return run.team_snapshot or legacy_team_snapshot(
        coordinator_agent_profile_id=run.root_agent_id or DEFAULT_ASSISTANT_POLICY_ID,
        developer_agent_id=run.developer_agent_id,
        qa_agent_id=run.qa_agent_id,
    )


def legacy_projection(snapshot: CodingAgentTeamSnapshot) -> tuple[str, str]:
    """Return compatibility developer/QA IDs for clients that still read them."""

    candidate = next(
        (m for m in snapshot.members if m.capability == CodingTeamMemberCapability.CANDIDATE),
        snapshot.members[0],
    )
    review = next(
        (
            m
            for m in snapshot.members
            if m.capability == CodingTeamMemberCapability.REVIEW and m.id != candidate.id
        ),
        candidate,
    )
    return candidate.agent_profile_id, review.agent_profile_id


class CodingTeamAdmission:
    """Read-side team resolver shared by presets, capabilities and run admission."""

    def __init__(self, *, repository: Any, validator: CodingAgentTeamValidator) -> None:
        self._repository = repository
        self._validator = validator

    def list(self, *, workspace_id: str) -> list[CodingAgentTeam]:
        teams: list[CodingAgentTeam] = self._repository.list(workspace_id=workspace_id)
        return teams

    def snapshot(self, *, workspace_id: str, team_id: str) -> CodingAgentTeamSnapshot:
        team, revision = self._repository.active_revision(
            workspace_id=workspace_id, team_id=team_id
        )
        self._validator.validate(revision=revision)
        return team_snapshot(revision, team_name=team.name)
