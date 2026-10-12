"""Shared authorization path for web-chat and workflow task delegation."""

from __future__ import annotations

from uuid import uuid4

from rhetorica_backend.contracts.coding import (
    AgentTask,
    CodingRunAdmission,
    CodingRunStatus,
    CreateCodingDelegationRequest,
    ExecutionPrincipal,
    GovernedAgentSettings,
)
from rhetorica_backend.domain.coding.errors import CodingDelegationDenied
from rhetorica_backend.infra.postgres.agent_policy_repository import AgentPolicyRepository
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository


class CodingDelegationService:
    """Create a child task only after repeating live access and policy checks.

    Both a web-chat route and a Temporal activity use the same method with the
    original authenticated web principal. There is deliberately no service
    identity parameter that could turn a worker retry into a privilege gain.
    """

    def __init__(
        self,
        *,
        runs: CodingRunRepository,
        users: UserAccountRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
        policies: AgentPolicyRepository,
    ) -> None:
        self._runs = runs
        self._users = users
        self._workspaces = workspaces
        self._agents = agents
        self._policies = policies

    def delegate(
        self,
        *,
        principal: ExecutionPrincipal,
        request: CreateCodingDelegationRequest,
    ) -> AgentTask:
        self._require_authenticated_web_context(principal)
        if principal.run_id != request.run_id or principal.task_id != request.parent_task_id:
            raise CodingDelegationDenied("coding_delegation_principal_mismatch")
        parent = self._runs.get_task(
            task_id=request.parent_task_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        if parent.run_id != request.run_id:
            raise CodingDelegationDenied("coding_delegation_parent_run_mismatch")
        if parent.parent_task_id is not None:
            raise CodingDelegationDenied("coding_delegation_depth_exceeded")
        if parent.status in {
            CodingRunStatus.CANCEL_REQUESTED,
            CodingRunStatus.CANCELLED,
            CodingRunStatus.FAILED,
            CodingRunStatus.REJECTED,
            CodingRunStatus.SUCCEEDED,
        }:
            raise CodingDelegationDenied("coding_delegation_parent_not_active")
        child = self._agents.get(
            agent_id=request.child_agent_profile_id,
            workspace_id=principal.workspace_id,
            current_user_id=principal.actor_user_id,
            active_only=True,
        )
        if child is None:
            raise CodingDelegationDenied("coding_delegation_child_unavailable")
        parent_policy = self._policies.get_agent_policy(
            workspace_id=principal.workspace_id,
            agent_id=parent.agent_profile_id,
        )
        if parent_policy is None or child.id not in parent_policy.allowed_subagent_ids:
            raise CodingDelegationDenied("coding_delegation_not_allowed")
        child_policy = self._policies.get_agent_policy(
            workspace_id=principal.workspace_id,
            agent_id=child.id,
        )
        if child_policy is None:
            # The profile was already checked above; this remains fail-closed
            # if its policy row disappears between the two live checks.
            raise CodingDelegationDenied("coding_delegation_child_policy_unavailable")
        admission = self._runs.get_admission(
            run_id=request.run_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        budget_settings = self._root_budget_settings(admission)
        task_settings = self._specialist_task_settings(
            budget_settings=budget_settings,
            specialist_settings=child_policy,
        )
        return self._runs.create_task(
            AgentTask(
                id=str(uuid4()),
                workspace_id=principal.workspace_id,
                owner_user_id=principal.actor_user_id,
                run_id=request.run_id,
                parent_task_id=parent.id,
                agent_profile_id=child.id,
                role=request.role,
                status=CodingRunStatus.QUEUED,
                max_iterations=task_settings.max_iterations,
                max_runtime_seconds=task_settings.max_runtime_seconds,
                instruction_artifact_id=request.instruction_artifact_id,
                skill_version_digests=request.skill_version_digests,
                skill_version_bindings=request.skill_version_bindings,
                skill_read_addresses=request.skill_read_addresses,
            )
        )

    def _require_authenticated_web_context(self, principal: ExecutionPrincipal) -> None:
        if principal.auth_kind != "session" or principal.entry_source not in {
            "web_chat",
            "web_scenario",
        }:
            raise CodingDelegationDenied("coding_delegation_web_context_required")
        user = self._users.get_by_id(user_id=principal.actor_user_id)
        if user is None or not user.is_active:
            raise CodingDelegationDenied("coding_delegation_actor_unavailable")
        if (
            self._workspaces.membership(
                workspace_id=principal.workspace_id,
                user_id=principal.actor_user_id,
            )
            is None
        ):
            raise CodingDelegationDenied("coding_delegation_workspace_access_denied")

    @staticmethod
    def _root_budget_settings(admission: CodingRunAdmission) -> GovernedAgentSettings:
        """Use the admitted root policy, never a later child-policy update."""

        snapshot = admission.policy_snapshot
        budget_owner_agent_id = (
            admission.run.root_agent_id or snapshot.root_agent_id or snapshot.executing_agent_id
        )
        if snapshot.root_agent_id == budget_owner_agent_id and snapshot.root_settings is not None:
            return snapshot.root_settings
        if snapshot.executing_agent_id == budget_owner_agent_id:
            return snapshot.executing_settings
        raise CodingDelegationDenied("coding_delegation_budget_owner_invalid")

    @staticmethod
    def _specialist_task_settings(
        *,
        budget_settings: GovernedAgentSettings,
        specialist_settings: GovernedAgentSettings,
    ) -> GovernedAgentSettings:
        """Keep the shared root budget while preserving stricter child caps."""

        return specialist_settings.model_copy(
            update={
                "max_iterations": min(
                    budget_settings.max_iterations,
                    specialist_settings.max_iterations,
                ),
                "max_runtime_seconds": min(
                    budget_settings.max_runtime_seconds,
                    specialist_settings.max_runtime_seconds,
                ),
            }
        )
