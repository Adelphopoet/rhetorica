from __future__ import annotations

import pytest
from rhetorica_backend.contracts.coding import (
    AgentDelegationGrant,
    AgentPolicyRecord,
    AgentPolicySnapshot,
    CodingReasonCode,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
)
from rhetorica_backend.domain.coding.policies import (
    AdminAgentPolicyService,
    AgentPolicySubject,
    AgentPolicyValidationError,
    AgentPublicationPolicyEvaluator,
)


def _scope(*paths: str, branches: tuple[str, ...] = ("main",)) -> GitLabWriteScope:
    return GitLabWriteScope(
        connector_id="gitlab-1",
        project_id="analytics/dbt",
        target_branches=list(branches),
        allowed_paths=list(paths),
    )


def _subject(agent_id: str, *connector_ids: str, active: bool = True) -> AgentPolicySubject:
    return AgentPolicySubject(
        agent_id=agent_id,
        workspace_id="analytics",
        is_active=active,
        allowed_gitlab_connector_ids=frozenset(connector_ids),
    )


def _principal(*, delegated: bool) -> ExecutionPrincipal:
    return ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        root_agent_id="manager" if delegated else "developer",
        parent_agent_id="manager" if delegated else None,
        executing_agent_id="developer",
        run_id="run-1",
        task_id="task-1",
    )


def _delegated_settings(*, revision: int = 1, paths: tuple[str, ...] = ("models",)):
    return GovernedAgentSettings(
        revision=revision,
        allowed_subagent_ids=["developer"],
        delegation_grants=[
            AgentDelegationGrant(
                subagent_id="developer",
                gitlab_write_scopes=[_scope(*paths)],
            )
        ],
    )


def test_admin_policy_rejects_inaccessible_connector_and_inactive_child() -> None:
    service = AdminAgentPolicyService()
    settings = GovernedAgentSettings(
        allowed_subagent_ids=["developer"],
        gitlab_write_scopes=[_scope("models")],
    )
    subjects = [_subject("manager", "gitlab-1"), _subject("developer", active=False)]

    with pytest.raises(AgentPolicyValidationError, match="unknown_allowed_subagent"):
        service.validate_agent_update(
            workspace_id="analytics",
            target_agent_id="manager",
            requested=settings,
            subjects=subjects,
            current_policies=[
                AgentPolicyRecord(agent_id="manager", settings=GovernedAgentSettings()),
                AgentPolicyRecord(agent_id="developer", settings=GovernedAgentSettings()),
            ],
            accessible_connector_ids={"gitlab-1"},
        )

    with pytest.raises(AgentPolicyValidationError, match="gitlab_connector_not_accessible"):
        service.validate_agent_update(
            workspace_id="analytics",
            target_agent_id="manager",
            requested=GovernedAgentSettings(gitlab_write_scopes=[_scope("models")]),
            subjects=[_subject("manager", "gitlab-1")],
            current_policies=[
                AgentPolicyRecord(agent_id="manager", settings=GovernedAgentSettings())
            ],
            accessible_connector_ids=set(),
        )


def test_admin_policy_validates_candidate_graph_for_cycles() -> None:
    service = AdminAgentPolicyService()
    with pytest.raises(AgentPolicyValidationError, match="agent_delegation_cycle_forbidden"):
        service.validate_agent_update(
            workspace_id="analytics",
            target_agent_id="manager",
            requested=GovernedAgentSettings(allowed_subagent_ids=["developer"]),
            subjects=[_subject("manager"), _subject("developer")],
            current_policies=[
                AgentPolicyRecord(
                    agent_id="developer",
                    settings=GovernedAgentSettings(allowed_subagent_ids=["manager"]),
                )
            ],
            accessible_connector_ids=set(),
        )


def test_direct_policy_uses_snapshot_live_and_run_intersection() -> None:
    admission_settings = GovernedAgentSettings(
        revision=3,
        gitlab_write_scopes=[_scope("models", "tests")],
    )
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="developer",
        executing_agent_id="developer",
        executing_settings=admission_settings,
        resource_connector_id="gitlab-1",
        run_scope=_scope("models/marts"),
    )
    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=_principal(delegated=False),
        admission=snapshot,
        current_executing=GovernedAgentSettings(
            revision=4,
            gitlab_write_scopes=[_scope("models")],
        ),
        current_root=None,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=True,
        root_agent_active=True,
    )

    assert decision.allowed is True
    assert decision.effective_scopes[0].allowed_paths == ["models/marts"]
    assert decision.policy_revisions == {"developer": 4}


def test_live_expansion_does_not_expand_admission_snapshot() -> None:
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="developer",
        executing_agent_id="developer",
        executing_settings=GovernedAgentSettings(
            revision=1,
            gitlab_write_scopes=[_scope("models")],
        ),
        resource_connector_id="gitlab-1",
        run_scope=_scope("tests"),
    )
    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=_principal(delegated=False),
        admission=snapshot,
        current_executing=GovernedAgentSettings(
            revision=2,
            gitlab_write_scopes=[_scope("models", "tests")],
        ),
        current_root=None,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=True,
        root_agent_active=True,
    )

    assert decision.allowed is False
    assert decision.reason_code == CodingReasonCode.PUBLICATION_NOT_AUTHORIZED


def test_parent_without_direct_write_can_delegate_narrower_child_scope() -> None:
    child = GovernedAgentSettings(revision=2, gitlab_write_scopes=[_scope("models")])
    root = _delegated_settings(revision=5, paths=("models", "tests"))
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="manager",
        executing_agent_id="developer",
        root_settings=root,
        executing_settings=child,
        resource_connector_id="gitlab-1",
        run_scope=_scope("models/marts"),
    )

    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=_principal(delegated=True),
        admission=snapshot,
        current_executing=child,
        current_root=root,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=True,
        root_agent_active=True,
    )

    assert root.gitlab_write_scopes == []
    assert decision.allowed is True
    assert decision.effective_scopes[0].allowed_paths == ["models/marts"]


@pytest.mark.parametrize(
    ("current_root", "current_child", "resource_accessible", "reason"),
    [
        (
            GovernedAgentSettings(),
            GovernedAgentSettings(gitlab_write_scopes=[_scope("models")]),
            True,
            CodingReasonCode.PUBLICATION_NOT_AUTHORIZED,
        ),
        (
            _delegated_settings(),
            GovernedAgentSettings(),
            True,
            CodingReasonCode.PUBLICATION_NOT_AUTHORIZED,
        ),
        (
            _delegated_settings(),
            GovernedAgentSettings(gitlab_write_scopes=[_scope("models")]),
            False,
            CodingReasonCode.RESOURCE_ACCESS_REVOKED,
        ),
    ],
)
def test_delegated_policy_fails_closed_when_required_edge_is_missing(
    current_root: GovernedAgentSettings,
    current_child: GovernedAgentSettings,
    resource_accessible: bool,
    reason: CodingReasonCode,
) -> None:
    admission_root = _delegated_settings()
    admission_child = GovernedAgentSettings(gitlab_write_scopes=[_scope("models")])
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="manager",
        executing_agent_id="developer",
        root_settings=admission_root,
        executing_settings=admission_child,
        resource_connector_id="gitlab-1",
        run_scope=_scope("models"),
    )
    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=_principal(delegated=True),
        admission=snapshot,
        current_executing=current_child,
        current_root=current_root,
        actor_authorized=True,
        resource_accessible=resource_accessible,
        executing_agent_active=True,
        root_agent_active=True,
    )

    assert decision.allowed is False
    assert decision.reason_code == reason


def test_forged_or_nested_principal_is_denied() -> None:
    child = GovernedAgentSettings(gitlab_write_scopes=[_scope("models")])
    root = _delegated_settings()
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="manager",
        executing_agent_id="developer",
        root_settings=root,
        executing_settings=child,
        resource_connector_id="gitlab-1",
        run_scope=_scope("models"),
    )
    nested = _principal(delegated=True).model_copy(update={"root_agent_id": "grandparent"})

    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=nested,
        admission=snapshot,
        current_executing=child,
        current_root=root,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=True,
        root_agent_active=True,
    )

    assert decision.allowed is False
    assert decision.reason_code == CodingReasonCode.POLICY_DENIED


@pytest.mark.parametrize(("executing_active", "root_active"), [(False, True), (True, False)])
def test_deactivated_agent_is_denied_before_publication(
    executing_active: bool, root_active: bool
) -> None:
    child = GovernedAgentSettings(gitlab_write_scopes=[_scope("models")])
    root = _delegated_settings()
    snapshot = AgentPolicySnapshot(
        workspace_id="analytics",
        root_agent_id="manager",
        executing_agent_id="developer",
        root_settings=root,
        executing_settings=child,
        resource_connector_id="gitlab-1",
        run_scope=_scope("models"),
    )

    decision = AgentPublicationPolicyEvaluator().evaluate(
        principal=_principal(delegated=True),
        admission=snapshot,
        current_executing=child,
        current_root=root,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=executing_active,
        root_agent_active=root_active,
    )

    assert decision.allowed is False
    assert decision.reason_code == CodingReasonCode.POLICY_REFERENCE_INVALID
