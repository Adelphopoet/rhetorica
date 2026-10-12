"""Team administration, validation and run admission without implicit grants."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.coding import (
    AgentGovernedPolicy,
    CodingAgentTeamRevision,
    CodingAgentTeamSnapshot,
    CodingResourceSelection,
    CreateCodingRunRequest,
    EnvironmentProfile,
    GitLabWriteScope,
    UpsertCodingAgentTeamRevision,
)
from rhetorica_backend.domain.coding.errors import (
    CodingRunError,
    CodingRunNotFound,
    CodingTeamValidationError,
)
from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID
from rhetorica_backend.domain.coding.resources import CodingResourcePolicy
from rhetorica_backend.domain.coding.service import CodingRunService
from rhetorica_backend.domain.coding.team_service import CodingAgentTeamService
from rhetorica_backend.domain.coding.teams import CodingAgentTeamValidator, CodingTeamAdmission

MEMBERS = [
    {
        "id": "engineer",
        "agent_profile_id": "dev",
        "label": "Data engineer",
        "instruction": "Check dbt changes.",
        "capability": "candidate",
        "skill_version_bindings": [{"skill_id": "dbt-core", "version_digest": "v1"}],
    },
    {"id": "reviewer", "agent_profile_id": "qa", "label": "Reviewer", "capability": "review"},
]


def _upsert(**overrides) -> UpsertCodingAgentTeamRevision:
    return UpsertCodingAgentTeamRevision.model_validate({
        "expected_revision": 0,
        "name": "dbt team",
        "coordinator_agent_profile_id": "root",
        "members": MEMBERS,
        **overrides,
    })


def _revision(**overrides) -> CodingAgentTeamRevision:
    return CodingAgentTeamRevision.model_validate({
        "id": "revision-1",
        "workspace_id": "workspace",
        "team_id": "team-1",
        "revision": 1,
        "coordinator_agent_profile_id": "root",
        "members": MEMBERS,
        "created_at": datetime.now(UTC),
        **overrides,
    })


def _profile(agent_id, *, gitlab=("gitlab",), skills=("dbt-core",), databases=()):
    return SimpleNamespace(
        id=agent_id,
        title=agent_id,
        allowed_gitlab_connector_names=list(gitlab),
        allowed_database_connector_names=list(databases),
        allowed_workspace_skill_ids=list(skills),
        settings=SimpleNamespace(run_sql_allowed=True),
    )


def _validator(*, profiles=None, allowed=("dev", "qa"), skill_version=True):
    profiles = profiles or {name: _profile(name) for name in ["root", "dev", "qa"]}
    return CodingAgentTeamValidator(
        agents=SimpleNamespace(get=lambda **kwargs: profiles.get(kwargs["agent_id"])),
        policies=SimpleNamespace(
            get_agent_policy=lambda **kwargs: SimpleNamespace(allowed_subagent_ids=list(allowed)),
            get_default_assistant_policy=lambda **kwargs: SimpleNamespace(
                allowed_subagent_ids=list(allowed)
            ),
        ),
        skills=SimpleNamespace(get_version=lambda **kwargs: object() if skill_version else None),
        gitlab_connectors=SimpleNamespace(
            list=lambda workspace_id, limit: [SimpleNamespace(name="gitlab", is_active=True)]
        ),
    )


@pytest.mark.parametrize(
    ("members", "code"),
    [
        ([MEMBERS[0], {**MEMBERS[1], "id": "engineer"}], "coding_team_member_id_duplicate"),
        (
            [MEMBERS[0], {**MEMBERS[1], "agent_profile_id": "dev"}],
            "coding_team_member_profile_duplicate",
        ),
        (
            [MEMBERS[0], {**MEMBERS[1], "agent_profile_id": "root"}],
            "coding_team_coordinator_member_duplicate",
        ),
        ([{**MEMBERS[0], "id": "has space"}], "string_pattern_mismatch"),
    ],
)
def test_revision_request_rejects_unsafe_topology_before_service(members, code) -> None:
    with pytest.raises(ValidationError, match=code):
        _upsert(members=members)


def test_member_capability_not_label_selects_the_tool_boundary() -> None:
    reviewer = _upsert(members=[{**MEMBERS[1], "label": "Developer who reviews"}]).members[0]
    assert reviewer.storage_role == "qa"


def test_validator_names_member_without_gitlab_grant() -> None:
    profiles = {"root": _profile("root"), "dev": _profile("dev"), "qa": _profile("qa", gitlab=())}
    with pytest.raises(CodingTeamValidationError) as error:
        _validator(profiles=profiles).validate(revision=_revision())
    assert error.value.detail == {
        "code": "coding_team_member_gitlab_grant_missing",
        "member_id": "reviewer",
        "member_label": "Reviewer",
    }


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"allowed": ("qa",)}, "coding_team_delegation_not_allowed"),
        ({"skill_version": False}, "coding_team_skill_version_unavailable"),
        (
            {
                "profiles": {
                    "root": _profile("root"),
                    "dev": _profile("dev", skills=()),
                    "qa": _profile("qa"),
                }
            },
            "coding_team_skill_not_allowed",
        ),
        (
            {"profiles": {"root": _profile("root"), "qa": _profile("qa")}},
            "coding_team_member_unavailable",
        ),
    ],
)
def test_validator_reuses_existing_policy_and_skill_grants(kwargs, code) -> None:
    with pytest.raises(CodingTeamValidationError, match=code):
        _validator(**kwargs).validate(revision=_revision())


def test_default_assistant_can_coordinate_with_its_delegation_policy() -> None:
    _validator().validate(
        revision=_revision(coordinator_agent_profile_id=DEFAULT_ASSISTANT_POLICY_ID)
    )


def _team_service(*, admin=True, validator=None, repository=None):
    coding = SimpleNamespace(require_actor=Mock(return_value=SimpleNamespace(is_admin=admin)))
    return CodingAgentTeamService(
        coding_service=coding,
        repository=repository or Mock(),
        validator=validator or Mock(),
    )


def test_only_system_admin_reads_creates_or_activates_revisions() -> None:
    repository = Mock()
    service = _team_service(admin=False, repository=repository)
    user = SimpleNamespace(id="user", workspace_id="workspace")
    with pytest.raises(CodingRunError, match="admin_access_required"):
        service.list(user)
    with pytest.raises(CodingRunError, match="admin_access_required"):
        service.detail(user, team_id="team-1")
    repository.list.assert_not_called()
    repository.detail.assert_not_called()
    with pytest.raises(CodingRunError, match="admin_access_required"):
        service.create_revision(user, _upsert())
    with pytest.raises(CodingRunError, match="admin_access_required"):
        service.activate(user, team_id="team-1", revision_id="revision-1")


def test_invalid_revision_is_not_persisted() -> None:
    validator = Mock()
    validator.validate.side_effect = CodingTeamValidationError(
        "coding_team_delegation_not_allowed", member_id="reviewer", member_label="Reviewer"
    )
    repository = Mock()
    service = _team_service(validator=validator, repository=repository)
    with pytest.raises(CodingTeamValidationError):
        service.create_revision(SimpleNamespace(id="admin", workspace_id="workspace"), _upsert())
    repository.append_revision.assert_not_called()


def test_revoked_grant_keeps_previously_active_revision() -> None:
    validator = Mock()
    validator.validate.side_effect = CodingTeamValidationError("coding_team_member_unavailable")
    repository = Mock()
    repository.get_revision.return_value = _revision(id="revision-2", revision=2)
    service = _team_service(validator=validator, repository=repository)
    with pytest.raises(CodingTeamValidationError):
        service.activate(
            SimpleNamespace(id="admin", workspace_id="workspace"),
            team_id="team-1",
            revision_id="revision-2",
        )
    repository.activate.assert_not_called()


def test_revision_of_another_team_cannot_be_activated() -> None:
    repository = Mock()
    repository.get_revision.return_value = _revision(team_id="other-team")
    service = _team_service(repository=repository)
    with pytest.raises(CodingRunError, match="coding_team_revision_not_found"):
        service.activate(
            SimpleNamespace(id="admin", workspace_id="workspace"),
            team_id="team-1",
            revision_id="revision-1",
        )
    repository.activate.assert_not_called()


def _policy(agent_id, *, subagents=(), paths=("models",)):
    return AgentGovernedPolicy(
        workspace_id="workspace",
        agent_id=agent_id,
        allowed_subagent_ids=list(subagents),
        gitlab_write_scopes=[
            GitLabWriteScope(
                connector_id="gitlab",
                project_id="group/project",
                target_branches=["main"],
                allowed_paths=list(paths),
            )
        ],
        max_iterations=20,
        max_runtime_seconds=600,
    )


def _resources(*, subagents=("dev", "qa"), team_validator=None):
    profiles = {name: _profile(name) for name in ["root", "dev", "qa"]}
    agents = SimpleNamespace(get=lambda **kwargs: profiles.get(kwargs["agent_id"]))
    policies = {
        "root": _policy("root", subagents=subagents),
        "dev": _policy("dev", paths=("models", "tests")),
        "qa": _policy("qa"),
    }
    policy_repository = SimpleNamespace(
        get_agent_policy=lambda **kwargs: policies.get(kwargs["agent_id"]),
        get_default_assistant_policy=lambda **kwargs: policies["root"],
    )
    team = CodingAgentTeamSnapshot.model_validate({
        "team_id": "team-1",
        "team_name": "dbt team",
        "revision_id": "revision-2",
        "revision": 2,
        "coordinator_agent_profile_id": "root",
        "members": MEMBERS,
    })
    teams = SimpleNamespace(
        snapshot=Mock(return_value=team),
        list=Mock(
            return_value=[
                SimpleNamespace(id="team-1", name="dbt team", active_revision_id="revision-2")
            ]
        ),
    )
    environment = EnvironmentProfile(
        id="env",
        revision=1,
        image_digest="registry.example/coding@sha256:" + "c" * 64,
        runtime_class_name="gvisor",
        max_runtime_seconds=1200,
        cpu_limit="1",
        memory_limit="1Gi",
        disk_limit_bytes=1024,
        pid_limit=64,
        created_at=datetime.now(UTC),
    )
    resource_policy = CodingResourcePolicy(
        users=SimpleNamespace(
            get_by_id=lambda user_id: SimpleNamespace(
                is_active=True,
                for_workspace=lambda workspace_id: SimpleNamespace(
                    id="user", workspace_id=workspace_id, is_admin=False
                ),
            )
        ),
        workspaces=SimpleNamespace(membership=lambda **kwargs: SimpleNamespace(role="analyst")),
        agents=agents,
        policies=policy_repository,
        gitlab_connectors=SimpleNamespace(
            list=lambda workspace_id, limit: [
                SimpleNamespace(id="gitlab", name="gitlab", is_active=True)
            ]
        ),
        databases=SimpleNamespace(list=lambda workspace_id, limit: []),
        environments=SimpleNamespace(
            get=lambda profile_id: environment, list_latest=lambda: [environment]
        ),
        enabled=True,
        teams=teams,
    )
    return resource_policy, policy_repository, agents, team


def _request(**overrides) -> CreateCodingRunRequest:
    return CreateCodingRunRequest.model_validate({
        "task": "Add net_revenue",
        "root_agent_id": "root",
        "coding_team_id": "team-1",
        "gitlab_connector_id": "gitlab",
        "project_id": "group/project",
        "target_branch": "main",
        "environment_profile_id": "env",
        **overrides,
    })


def test_selection_requires_a_team_or_the_legacy_pair() -> None:
    with pytest.raises(ValidationError, match="coding_team_required"):
        _request(coding_team_id=None)
    assert _request(coding_team_id=None, developer_agent_id="dev", qa_agent_id="qa")


def test_team_coordinator_must_be_the_selected_root() -> None:
    resources, *_ = _resources()
    user = SimpleNamespace(id="user", workspace_id="workspace")
    with pytest.raises(CodingTeamValidationError, match="coding_team_coordinator_mismatch"):
        resources.admit_resources(_request(root_agent_id=DEFAULT_ASSISTANT_POLICY_ID), user)


def test_revoked_delegation_rejects_admission_and_names_member() -> None:
    resources, *_ = _resources(subagents=("dev",))
    user = SimpleNamespace(id="user", workspace_id="workspace")
    with pytest.raises(CodingTeamValidationError) as error:
        resources.admit_resources(_request(), user)
    assert error.value.code == "coding_delegation_not_allowed"
    assert error.value.member_label == "Reviewer"


def test_capabilities_show_team_availability_and_reason() -> None:
    resources, *_ = _resources(subagents=("dev",))
    user = SimpleNamespace(id="user", workspace_id="workspace")
    result = resources.capabilities(user, root_agent_id="root")
    assert result["can_start_coding"] is False
    assert result["teams"][0]["available"] is False
    assert result["teams"][0]["unavailable_member"] == "Reviewer"

    resources, *_ = _resources()
    result = resources.capabilities(user, root_agent_id="root")
    assert result["can_start_coding"] is True
    assert [m["label"] for m in result["teams"][0]["members"]] == ["Data engineer", "Reviewer"]


def test_run_admission_snapshots_team_and_creates_labeled_member_tasks() -> None:
    resource_policy, policies, agents, team = _resources()
    service = object.__new__(CodingRunService)
    service.resources = resource_policy
    service.enabled = True
    service.policies = policies
    service.agents = agents
    service.skills = SimpleNamespace(list_active_chat_instructions=lambda **kwargs: [])
    service.runs = Mock()
    service.runs.get_admission.side_effect = CodingRunNotFound()
    service.runs.admit.side_effect = lambda admission, tasks: (admission, tasks)
    service.gitlab_connectors = SimpleNamespace(get_connection_by_id=lambda **kwargs: object())
    service.gitlab = SimpleNamespace(
        get_branch=lambda *args, **kwargs: {"commit": {"id": "b" * 40}}
    )
    service.artifacts = SimpleNamespace(persist=lambda **kwargs: "artifact://instructions")

    admission, tasks = service.create(
        _request(),
        SimpleNamespace(id="user", workspace_id="workspace"),
        auth_kind="session",
        idempotency_key="key",
        source="web_scenario",
        scenario_id="scenario",
    )

    assert admission.run.team_snapshot == team
    assert (admission.run.developer_agent_id, admission.run.qa_agent_id) == ("dev", "qa")
    # Publication scope is the intersection of candidate members' existing grants.
    assert admission.policy_snapshot.run_scope.allowed_paths == ["models", "tests"]
    members = [task for task in tasks if task.role != "coordinator"]
    assert [(t.team_member_id, t.team_member_label, t.role) for t in members] == [
        ("engineer", "Data engineer", "developer"),
        ("reviewer", "Reviewer", "qa"),
    ]
    assert [b.skill_id for b in members[0].skill_version_bindings] == ["dbt-core"]
    assert all(task.parent_task_id == tasks[0].id for task in members)


def test_team_admission_resolves_active_revision_and_revalidates() -> None:
    repository = Mock()
    repository.active_revision.return_value = (
        SimpleNamespace(name="dbt team"),
        _revision(id="revision-2", revision=2),
    )
    validator = Mock()
    snapshot = CodingTeamAdmission(repository=repository, validator=validator).snapshot(
        workspace_id="workspace", team_id="team-1"
    )
    assert (snapshot.revision_id, snapshot.team_name) == ("revision-2", "dbt team")
    validator.validate.assert_called_once()


def test_chat_team_options_name_the_coordinator_to_converse_with() -> None:
    resources, *_ = _resources()
    user = SimpleNamespace(id="user", workspace_id="workspace")

    options = resources.team_options(user)

    assert options == [
        {
            "id": "team-1",
            "name": "dbt team",
            "active_revision_id": "revision-2",
            "available": True,
            "revision": 2,
            "coordinator_agent_profile_id": "root",
            "members": [
                {
                    "id": "engineer",
                    "label": "Data engineer",
                    "capability": "candidate",
                    "agent_profile_id": "dev",
                },
                {
                    "id": "reviewer",
                    "label": "Reviewer",
                    "capability": "review",
                    "agent_profile_id": "qa",
                },
            ],
        }
    ]


def test_selected_coordinator_uses_its_only_team_when_preset_names_another() -> None:
    resources, *_ = _resources()
    preset = _request(coding_team_id="other-team").model_dump(exclude={"task", "conversation_id"})
    resources.workspace_defaults = SimpleNamespace(
        get=lambda workspace_id: SimpleNamespace(
            revision=4, resources=CodingResourceSelection.model_validate(preset)
        )
    )
    original = resources.teams.snapshot.return_value
    resources.teams.snapshot.side_effect = lambda **kwargs: original

    result = resources.capabilities(
        SimpleNamespace(id="user", workspace_id="workspace"), root_agent_id="root"
    )

    assert result["launch_defaults"]["coding_team_id"] == "team-1"
    assert result["defaults_unavailable_reason"] is None


def test_members_without_one_shared_gitlab_connector_are_rejected() -> None:
    profiles = {
        "root": _profile("root", gitlab=("gitlab", "gitlab-2")),
        "dev": _profile("dev", gitlab=("gitlab",)),
        "qa": _profile("qa", gitlab=("gitlab-2",)),
    }
    validator = _validator(profiles=profiles)
    validator._gitlab_connectors = SimpleNamespace(
        list=lambda workspace_id, limit: [
            SimpleNamespace(name="gitlab", is_active=True),
            SimpleNamespace(name="gitlab-2", is_active=True),
        ]
    )
    with pytest.raises(CodingTeamValidationError) as error:
        validator.validate(revision=_revision())
    assert (error.value.code, error.value.member_label) == (
        "coding_team_member_gitlab_grant_missing",
        "Reviewer",
    )
