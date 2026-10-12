"""Deterministic administration and runtime evaluation for coding policies."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from rhetorica_backend.contracts.coding import (
    AgentDelegationGrant,
    AgentPolicyGraph,
    AgentPolicyRecord,
    AgentPolicySnapshot,
    CodingReasonCode,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    PublicationPolicyDecision,
)

DEFAULT_ASSISTANT_POLICY_ID = "__default_assistant__"


class AgentPolicyValidationError(ValueError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class AgentPolicySubject:
    agent_id: str
    workspace_id: str
    is_active: bool
    allowed_gitlab_connector_ids: frozenset[str]


class AdminAgentPolicyService:
    """Validate a complete candidate graph loaded inside a repository transaction."""

    def validate_agent_update(
        self,
        *,
        workspace_id: str,
        target_agent_id: str,
        requested: GovernedAgentSettings,
        subjects: list[AgentPolicySubject],
        current_policies: list[AgentPolicyRecord],
        accessible_connector_ids: set[str],
    ) -> None:
        subject_by_id = {subject.agent_id: subject for subject in subjects}
        target = subject_by_id.get(target_agent_id)
        if target is None or target.workspace_id != workspace_id or not target.is_active:
            raise AgentPolicyValidationError("agent_policy_target_not_found")
        self._validate_references(
            workspace_id=workspace_id,
            requested=requested,
            target=target,
            subject_by_id=subject_by_id,
            accessible_connector_ids=accessible_connector_ids,
        )
        graph_records = [
            record for record in current_policies if record.agent_id != target_agent_id
        ]
        graph_records.append(AgentPolicyRecord(agent_id=target_agent_id, settings=requested))
        try:
            AgentPolicyGraph(agents=graph_records)
        except ValidationError as exc:
            reason = self._pydantic_reason(exc)
            raise AgentPolicyValidationError(reason) from exc

    def validate_default_assistant_update(
        self,
        *,
        workspace_id: str,
        requested: GovernedAgentSettings,
        subjects: list[AgentPolicySubject],
        current_policies: list[AgentPolicyRecord],
        accessible_connector_ids: set[str],
    ) -> None:
        virtual_target = AgentPolicySubject(
            agent_id=DEFAULT_ASSISTANT_POLICY_ID,
            workspace_id=workspace_id,
            is_active=True,
            allowed_gitlab_connector_ids=frozenset(accessible_connector_ids),
        )
        subject_by_id = {subject.agent_id: subject for subject in subjects}
        self._validate_references(
            workspace_id=workspace_id,
            requested=requested,
            target=virtual_target,
            subject_by_id=subject_by_id,
            accessible_connector_ids=accessible_connector_ids,
        )
        graph_records = [
            record for record in current_policies if record.agent_id != DEFAULT_ASSISTANT_POLICY_ID
        ]
        graph_records.append(
            AgentPolicyRecord(agent_id=DEFAULT_ASSISTANT_POLICY_ID, settings=requested)
        )
        try:
            AgentPolicyGraph(agents=graph_records)
        except ValidationError as exc:
            reason = self._pydantic_reason(exc)
            raise AgentPolicyValidationError(reason) from exc

    @staticmethod
    def _validate_references(
        *,
        workspace_id: str,
        requested: GovernedAgentSettings,
        target: AgentPolicySubject,
        subject_by_id: dict[str, AgentPolicySubject],
        accessible_connector_ids: set[str],
    ) -> None:
        for child_id in requested.allowed_subagent_ids:
            child = subject_by_id.get(child_id)
            if child is None or child.workspace_id != workspace_id or not child.is_active:
                raise AgentPolicyValidationError("unknown_allowed_subagent")
            if child.agent_id == target.agent_id:
                raise AgentPolicyValidationError("agent_self_delegation_forbidden")

        for scope in requested.gitlab_write_scopes:
            AdminAgentPolicyService._validate_scope_connector(
                scope=scope,
                accessible_connector_ids=accessible_connector_ids,
                allowed_connector_ids=target.allowed_gitlab_connector_ids,
            )
        for grant in requested.delegation_grants:
            child = subject_by_id.get(grant.subagent_id)
            if child is None:
                raise AgentPolicyValidationError("unknown_allowed_subagent")
            for scope in grant.gitlab_write_scopes:
                AdminAgentPolicyService._validate_scope_connector(
                    scope=scope,
                    accessible_connector_ids=accessible_connector_ids,
                    allowed_connector_ids=(
                        target.allowed_gitlab_connector_ids & child.allowed_gitlab_connector_ids
                    ),
                )

    @staticmethod
    def _validate_scope_connector(
        *,
        scope: GitLabWriteScope,
        accessible_connector_ids: set[str],
        allowed_connector_ids: frozenset[str],
    ) -> None:
        if scope.connector_id not in accessible_connector_ids:
            raise AgentPolicyValidationError("gitlab_connector_not_accessible")
        if scope.connector_id not in allowed_connector_ids:
            raise AgentPolicyValidationError("gitlab_connector_not_allowed_for_agent")

    @staticmethod
    def _pydantic_reason(exc: ValidationError) -> str:
        errors = exc.errors()
        if not errors:
            return "agent_policy_invalid"
        context = errors[0].get("ctx") or {}
        raw = context.get("error")
        return str(raw) if raw is not None else str(errors[0].get("type", "agent_policy_invalid"))


class AgentPublicationPolicyEvaluator:
    """Pure fail-closed evaluator used at admission and immediately before mutation."""

    def evaluate(
        self,
        *,
        principal: ExecutionPrincipal,
        admission: AgentPolicySnapshot,
        current_executing: GovernedAgentSettings,
        current_root: GovernedAgentSettings | None,
        actor_authorized: bool,
        resource_accessible: bool,
        executing_agent_active: bool,
        root_agent_active: bool,
    ) -> PublicationPolicyDecision:
        revisions = {principal.executing_agent_id or "executing": current_executing.revision}
        if current_root is not None and principal.parent_agent_id is not None:
            revisions[principal.parent_agent_id] = current_root.revision
        if not actor_authorized:
            return self._deny(CodingReasonCode.WORKSPACE_ACCESS_DENIED, revisions)
        if not resource_accessible:
            return self._deny(CodingReasonCode.RESOURCE_ACCESS_REVOKED, revisions)
        if not executing_agent_active or (
            principal.parent_agent_id is not None and not root_agent_active
        ):
            return self._deny(CodingReasonCode.POLICY_REFERENCE_INVALID, revisions)
        if principal.workspace_id != admission.workspace_id:
            return self._deny(CodingReasonCode.POLICY_DENIED, revisions)
        if principal.executing_agent_id != admission.executing_agent_id:
            return self._deny(CodingReasonCode.POLICY_DENIED, revisions)
        if admission.resource_connector_id != admission.run_scope.connector_id:
            return self._deny(CodingReasonCode.POLICY_DENIED, revisions)

        if principal.parent_agent_id is None:
            if principal.root_agent_id not in {None, principal.executing_agent_id}:
                return self._deny(CodingReasonCode.POLICY_DENIED, revisions)
            scopes = _intersect_scope_groups(
                admission.executing_settings.gitlab_write_scopes,
                current_executing.gitlab_write_scopes,
                [admission.run_scope],
            )
            return self._decision(scopes, revisions)

        if (
            principal.root_agent_id != principal.parent_agent_id
            or admission.root_agent_id != principal.parent_agent_id
            or admission.root_settings is None
            or current_root is None
        ):
            return self._deny(CodingReasonCode.POLICY_DENIED, revisions)
        child_id = principal.executing_agent_id
        if (
            child_id not in admission.root_settings.allowed_subagent_ids
            or child_id not in current_root.allowed_subagent_ids
        ):
            return self._deny(CodingReasonCode.PUBLICATION_NOT_AUTHORIZED, revisions)
        admission_grant = _grant_for(admission.root_settings, child_id)
        current_grant = _grant_for(current_root, child_id)
        if admission_grant is None or current_grant is None:
            return self._deny(CodingReasonCode.PUBLICATION_NOT_AUTHORIZED, revisions)
        scopes = _intersect_scope_groups(
            admission_grant.gitlab_write_scopes,
            current_grant.gitlab_write_scopes,
            admission.executing_settings.gitlab_write_scopes,
            current_executing.gitlab_write_scopes,
            [admission.run_scope],
        )
        return self._decision(scopes, revisions)

    @staticmethod
    def _decision(
        scopes: list[GitLabWriteScope], revisions: dict[str, int]
    ) -> PublicationPolicyDecision:
        if not scopes:
            return AgentPublicationPolicyEvaluator._deny(
                CodingReasonCode.PUBLICATION_NOT_AUTHORIZED, revisions
            )
        return PublicationPolicyDecision(
            allowed=True,
            effective_scopes=scopes,
            policy_revisions=revisions,
        )

    @staticmethod
    def _deny(reason: CodingReasonCode, revisions: dict[str, int]) -> PublicationPolicyDecision:
        return PublicationPolicyDecision(
            allowed=False,
            reason_code=reason,
            policy_revisions=revisions,
        )


def _grant_for(settings: GovernedAgentSettings, child_id: str) -> AgentDelegationGrant | None:
    return next(
        (grant for grant in settings.delegation_grants if grant.subagent_id == child_id),
        None,
    )


def _intersect_scope_groups(
    first: list[GitLabWriteScope], *rest: list[GitLabWriteScope]
) -> list[GitLabWriteScope]:
    result = list(first)
    for group in rest:
        result = [
            intersection
            for left in result
            for right in group
            if (intersection := _intersect_scope(left, right)) is not None
        ]
        result = _dedupe_scopes(result)
        if not result:
            break
    return result


def _intersect_scope(left: GitLabWriteScope, right: GitLabWriteScope) -> GitLabWriteScope | None:
    if left.connector_id != right.connector_id or left.project_id != right.project_id:
        return None
    branches = sorted(set(left.target_branches) & set(right.target_branches))
    if not branches:
        return None
    paths = sorted({
        path
        for left_path in left.allowed_paths
        for right_path in right.allowed_paths
        if (path := _narrower_path(left_path, right_path)) is not None
    })
    if not paths:
        return None
    return GitLabWriteScope(
        connector_id=left.connector_id,
        project_id=left.project_id,
        target_branches=branches,
        allowed_paths=paths,
    )


def _narrower_path(left: str, right: str) -> str | None:
    if left == right:
        return left
    if right.startswith(f"{left}/"):
        return right
    if left.startswith(f"{right}/"):
        return left
    return None


def _dedupe_scopes(scopes: list[GitLabWriteScope]) -> list[GitLabWriteScope]:
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    result: list[GitLabWriteScope] = []
    for scope in scopes:
        key = (
            scope.connector_id,
            scope.project_id,
            tuple(scope.target_branches),
            tuple(scope.allowed_paths),
        )
        if key not in seen:
            seen.add(key)
            result.append(scope)
    return result
