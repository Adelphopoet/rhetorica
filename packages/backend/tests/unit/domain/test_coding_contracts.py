from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.coding import (
    AgentDelegationGrant,
    AgentPolicyGraph,
    AgentPolicySnapshot,
    AgentTask,
    CodingAgentTeamRevision,
    CodingReasonCode,
    CodingRun,
    CodingRunAdmission,
    CodingTeamMemberCapability,
    DefaultAssistantPolicy,
    EnvironmentProfile,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    PublicationPolicyDecision,
    PublicationReceipt,
    SkillReadAddress,
    ValidationReport,
)
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillVersionBinding
from rhetorica_backend.contracts.workspaces import WorkspaceResourceGrantItem
from rhetorica_backend.domain.coding.admission import CodingAdmissionError, CodingAdmissionPolicy

FIXTURES = Path(__file__).parents[2] / "fixtures" / "coding"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_agent_policy_defaults_are_closed_and_bounded() -> None:
    settings = GovernedAgentSettings()
    default_assistant = DefaultAssistantPolicy(workspace_id="analytics")

    assert settings.gitlab_write_scopes == []
    assert settings.allowed_subagent_ids == []
    assert settings.delegation_grants == []
    assert settings.max_iterations == 100
    assert settings.max_runtime_seconds == 1200
    assert default_assistant.model_dump()["revision"] == 1


def test_team_revision_is_non_secret_and_rejects_duplicate_or_coordinator_membership() -> None:
    member = {
        "id": "member-1",
        "agent_profile_id": "developer",
        "label": "  dbt developer  ",
        "capability": "candidate",
        "skill_version_bindings": [{"skill_id": "dbt", "version_digest": "v1"}],
    }
    revision = CodingAgentTeamRevision(
        id="revision-1",
        workspace_id="analytics",
        team_id="team-1",
        revision=1,
        coordinator_agent_profile_id="coordinator",
        members=[member],
        created_at=datetime.now(UTC),
    )

    serialized = revision.model_dump(mode="json")
    assert revision.members[0].label == "dbt developer"
    assert revision.members[0].capability == CodingTeamMemberCapability.CANDIDATE
    assert not ({"token", "password", "credential", "secret"} & set(serialized))

    with pytest.raises(ValidationError, match="coding_team_member_profile_duplicate"):
        CodingAgentTeamRevision(
            id="revision-1",
            workspace_id="analytics",
            team_id="team-1",
            revision=1,
            coordinator_agent_profile_id="coordinator",
            members=[member, {**member, "id": "member-2"}],
            created_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError, match="coding_team_coordinator_member_duplicate"):
        CodingAgentTeamRevision(
            id="revision-1",
            workspace_id="analytics",
            team_id="team-1",
            revision=1,
            coordinator_agent_profile_id="developer",
            members=[member],
            created_at=datetime.now(UTC),
        )


def test_agent_policy_rejects_empty_unknown_self_and_cyclic_delegation() -> None:
    with pytest.raises(ValidationError, match="too_short"):
        AgentDelegationGrant(subagent_id="developer", gitlab_write_scopes=[])

    with pytest.raises(ValidationError, match="unknown_allowed_subagent"):
        AgentPolicyGraph.model_validate({
            "agents": [{"agent_id": "manager", "settings": {"allowed_subagent_ids": ["missing"]}}]
        })
    with pytest.raises(ValidationError, match="agent_self_delegation_forbidden"):
        AgentPolicyGraph.model_validate({
            "agents": [{"agent_id": "manager", "settings": {"allowed_subagent_ids": ["manager"]}}]
        })
    with pytest.raises(ValidationError, match="agent_delegation_cycle_forbidden"):
        AgentPolicyGraph.model_validate({
            "agents": [
                {"agent_id": "manager", "settings": {"allowed_subagent_ids": ["developer"]}},
                {"agent_id": "developer", "settings": {"allowed_subagent_ids": ["manager"]}},
            ]
        })


@pytest.mark.parametrize(
    "path",
    ["", "/models", "../models", "models/../secrets", ".git/config", "models\\x.sql"],
)
def test_gitlab_write_scope_rejects_unsafe_or_empty_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="gitlab_allowed_path"):
        GitLabWriteScope(
            connector_id="gitlab-1",
            project_id="analytics/dbt",
            target_branches=["main"],
            allowed_paths=[path],
        )


def test_gitlab_write_scope_normalizes_exact_values() -> None:
    scope = GitLabWriteScope(
        connector_id=" gitlab-1 ",
        project_id=" analytics/dbt ",
        target_branches=[" main ", "main"],
        allowed_paths=["models/", "models", "tests/schema.yml"],
    )

    assert scope.connector_id == "gitlab-1"
    assert scope.project_id == "analytics/dbt"
    assert scope.target_branches == ["main"]
    assert scope.allowed_paths == ["models", "tests/schema.yml"]


@pytest.mark.parametrize("branch", ["feature..x", "bad branch", "refs/@{1}", ".hidden"])
def test_gitlab_write_scope_rejects_invalid_git_branches(branch: str) -> None:
    with pytest.raises(ValidationError, match="gitlab_target_branch_invalid"):
        GitLabWriteScope(
            connector_id="gitlab-1",
            project_id="analytics/dbt",
            target_branches=[branch],
            allowed_paths=["models"],
        )


def test_policy_fixture_serializes_without_identity_or_credentials() -> None:
    fixture = _fixture("authorized_policy.json")
    snapshot = AgentPolicySnapshot.model_validate(fixture["snapshot"])
    decision = PublicationPolicyDecision.model_validate(fixture["decision"])
    serialized = json.dumps({
        "snapshot": snapshot.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    })

    assert decision.allowed is True
    assert snapshot.run_scope.allowed_paths == ["models/marts"]
    assert not any(name in serialized for name in ["api_token", "password", "credentials"])
    assert "actor_user_id" not in serialized


def test_coding_contract_fixtures_cover_terminal_and_partial_results() -> None:
    success = CodingRun.model_validate(_fixture("success.json"))
    denied = CodingRun.model_validate(_fixture("denied.json"))
    cancelled = CodingRun.model_validate(_fixture("cancelled.json"))
    report = ValidationReport.model_validate(_fixture("not_run_report.json"))
    partial = PublicationReceipt.model_validate(_fixture("partial_publication.json"))

    assert success.publication_receipt_id == "receipt-1"
    assert denied.reason_code == CodingReasonCode.PUBLICATION_NOT_AUTHORIZED
    assert cancelled.partial_result is not None
    assert report.checks[1].status == "not_run"
    assert partial.state == "commit_recorded"
    assert partial.reason_code == CodingReasonCode.RESOURCE_ACCESS_REVOKED


def test_coding_run_rejects_candidate_from_another_scope() -> None:
    payload = _fixture("success.json")
    candidate = payload["candidate"]
    assert isinstance(candidate, dict)
    candidate["workspace_id"] = "foreign"

    with pytest.raises(ValidationError, match="coding_candidate_ownership_mismatch"):
        CodingRun.model_validate(payload)


def test_execution_principal_accepts_only_server_web_session_sources() -> None:
    principal = {
        "actor_user_id": "user-1",
        "workspace_id": "analytics",
        "entry_source": "web_chat",
        "auth_kind": "session",
        "run_id": "run-1",
    }
    assert ExecutionPrincipal.model_validate(principal).workspace_id == "analytics"
    with pytest.raises(ValidationError):
        ExecutionPrincipal.model_validate({**principal, "entry_source": "mattermost"})
    with pytest.raises(ValidationError):
        ExecutionPrincipal.model_validate({**principal, "auth_kind": "personal_access_token"})


def test_workspace_resource_grant_has_no_credentials_surface() -> None:
    payload = WorkspaceResourceGrantItem.model_validate({
        "id": "grant-1",
        "resource_kind": "gitlab_connector",
        "resource_id": "connector-1",
        "owner_workspace_id": "platform",
        "recipient_workspace_id": "analytics",
        "alias": "gitlab",
        "is_active": True,
        "created_by": "user-1",
        "created_at": "2026-09-22T08:00:00Z",
        "updated_at": "2026-09-22T08:00:00Z",
    }).model_dump(mode="json")

    assert payload["resource_id"] == "connector-1"
    assert not ({"token", "password", "credentials", "secret"} & set(payload))


def test_disabled_admission_blocks_only_new_runs_with_typed_reason() -> None:
    with pytest.raises(CodingAdmissionError) as error:
        CodingAdmissionPolicy(enabled=False).require_new_run_enabled()

    assert error.value.reason_code == CodingReasonCode.CODING_DISABLED
    CodingAdmissionPolicy(enabled=True).require_new_run_enabled()


def test_agent_task_persists_skill_addresses_without_skill_content() -> None:
    task = AgentTask(
        id="task-1",
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id="run-1",
        agent_profile_id="developer",
        role="developer",
        status="queued",
        max_iterations=10,
        max_runtime_seconds=300,
        instruction_artifact_id="artifact://instruction-1",
        skill_version_digests=["digest-v1"],
        skill_version_bindings=[
            WorkspaceSkillVersionBinding(skill_id="skill-1", version_digest="digest-v1")
        ],
        skill_read_addresses=[
            SkillReadAddress(
                skill_id="skill-1",
                version_digest="digest-v1",
                path="rules/trino.md",
                offset=4_000,
                max_chars=4_000,
            )
        ],
    )

    persisted = task.model_dump(mode="json")

    assert persisted["skill_read_addresses"] == [
        {
            "skill_id": "skill-1",
            "version_digest": "digest-v1",
            "path": "rules/trino.md",
            "offset": 4_000,
            "max_chars": 4_000,
        }
    ]
    assert "content" not in str(persisted)


@pytest.mark.parametrize("path", ["/rules.md", "../rules.md", "rules/../secret.md", "rules\\x.md"])
def test_skill_read_address_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="workspace_skill_read_path_invalid"):
        SkillReadAddress(
            skill_id="skill-1",
            version_digest="digest-v1",
            path=path,
            offset=0,
            max_chars=1,
        )


def test_coding_admission_rejects_mismatched_server_owned_scope() -> None:
    run = CodingRun.model_validate(_fixture("success.json"))
    policy = AgentPolicySnapshot.model_validate(_fixture("authorized_policy.json")["snapshot"])
    principal = ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        root_agent_id="manager",
        parent_agent_id="manager",
        executing_agent_id="developer",
        run_id=run.id,
    )
    environment = EnvironmentProfile(
        id="dbt",
        revision=1,
        image_digest=f"registry.example/rhetorica@sha256:{'a' * 64}",
        runtime_class_name="gvisor",
        max_runtime_seconds=1_200,
        cpu_limit="1",
        memory_limit="1Gi",
        disk_limit_bytes=1_024,
        pid_limit=16,
        created_at="2026-09-22T08:00:00Z",
    )

    admitted = CodingRunAdmission(
        run=run,
        principal=principal,
        policy_snapshot=policy,
        environment_profile=environment,
    )

    assert admitted.run.id == "run-success"
    with pytest.raises(ValidationError, match="coding_admission_snapshot_scope_mismatch"):
        CodingRunAdmission(
            run=run,
            principal=principal.model_copy(update={"workspace_id": "foreign"}),
            policy_snapshot=policy,
            environment_profile=environment,
        )
