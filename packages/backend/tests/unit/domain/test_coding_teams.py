from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from rhetorica_backend.contracts.coding import CodingAgentTeamRevision
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.teams import CodingAgentTeamValidator


def _revision() -> CodingAgentTeamRevision:
    return CodingAgentTeamRevision.model_validate({
        "id": "revision",
        "team_id": "team",
        "workspace_id": "analytics",
        "revision": 1,
        "coordinator_agent_profile_id": "coordinator",
        "members": [
            {
                "id": "developer-member",
                "agent_profile_id": "developer",
                "label": "Developer",
                "capability": "candidate",
                "skill_version_bindings": [{"skill_id": "dbt", "version_digest": "v1"}],
            }
        ],
        "created_at": datetime.now(UTC),
    })


def test_team_validation_uses_existing_policy_and_skill_bindings_only() -> None:
    agents = SimpleNamespace(
        get=lambda **kwargs: SimpleNamespace(allowed_workspace_skill_ids=["dbt"])
    )
    policies = SimpleNamespace(
        get_agent_policy=lambda **kwargs: SimpleNamespace(allowed_subagent_ids=["developer"])
    )
    skills = SimpleNamespace(get_version=lambda **kwargs: SimpleNamespace())
    CodingAgentTeamValidator(agents=agents, policies=policies, skills=skills).validate(
        revision=_revision()
    )


def test_team_validation_rejects_member_outside_coordinator_policy() -> None:
    agents = SimpleNamespace(
        get=lambda **kwargs: SimpleNamespace(allowed_workspace_skill_ids=["dbt"])
    )
    policies = SimpleNamespace(
        get_agent_policy=lambda **kwargs: SimpleNamespace(allowed_subagent_ids=[])
    )
    skills = SimpleNamespace(get_version=lambda **kwargs: SimpleNamespace())

    with pytest.raises(CodingRunError, match="coding_team_delegation_not_allowed"):
        CodingAgentTeamValidator(agents=agents, policies=policies, skills=skills).validate(
            revision=_revision()
        )
