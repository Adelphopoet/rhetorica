"""Publication gates and persisted-reference checks independent of GitLab HTTP."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    CandidateManifest,
    CodingReasonCode,
    CodingRun,
    CodingRunStatus,
    ExecutionPrincipal,
    GovernedAgentSettings,
    PublicationPolicyDecision,
    PublicationReceipt,
    ValidationReport,
    ValidationStatus,
)
from rhetorica_backend.domain.coding.policies import AgentPublicationPolicyEvaluator
from rhetorica_backend.domain.coding.publication_content_policy import (
    PublicationContentPolicy,
    PublicationDenied,
)
from rhetorica_backend.domain.coding.publication_diff import (
    FileChange,
    diff_trees,
    tree_from_candidate_archive,
)
from rhetorica_backend.domain.coding.publication_mr import (
    commit_message,
    draft_mr_description,
    draft_mr_title,
    publication_branch_name,
)
from rhetorica_backend.infra.coding.archives import ArchivePolicy, UnsafeArchiveError

BLOCKING_RUN_CODES = {
    CodingReasonCode.ITERATION_LIMIT_REACHED,
    CodingReasonCode.RUNTIME_LIMIT_REACHED,
    CodingReasonCode.EXECUTION_FAILED,
    CodingReasonCode.CODING_DISABLED,
}
STOPPED_STATUSES = {CodingRunStatus.CANCEL_REQUESTED, CodingRunStatus.CANCELLED}


@dataclass(frozen=True)
class PublicationWorkItem:
    run: CodingRun
    admission: AgentPolicySnapshot
    current_executing: GovernedAgentSettings
    current_root: GovernedAgentSettings | None
    actor_authorized: bool
    resource_accessible: bool
    executing_agent_active: bool
    root_agent_active: bool
    validation_report: ValidationReport | None = None
    cancelled: bool = False


class PublicationWorkItemPort(Protocol):
    def load(self, *, run_id: str, principal: ExecutionPrincipal) -> PublicationWorkItem: ...


class PublicationLedgerPort(Protocol):
    def get(
        self, *, workspace_id: str, run_id: str, owner_user_id: str
    ) -> PublicationReceipt | None: ...
    def reserve(self, receipt: PublicationReceipt) -> PublicationReceipt: ...
    def save(
        self, receipt: PublicationReceipt, *, expected_state: str
    ) -> PublicationReceipt | None: ...


@dataclass(frozen=True)
class PreparedPublication:
    run_id: str
    branch_name: str
    commit_message: str
    title: str
    description_without_commit: str
    actions: tuple[FileChange, ...]
    tree_digest: str
    candidate_digest: str
    validation_report_digest: str
    base_sha: str
    project_id: str
    target_branch: str
    connector_id: str
    policy: PublicationPolicyDecision


class PublicationPreparer:
    def __init__(
        self,
        *,
        evaluator: AgentPublicationPolicyEvaluator | None = None,
        content_policy: PublicationContentPolicy | None = None,
        archive_policy: ArchivePolicy | None = None,
    ) -> None:
        self._evaluator = evaluator or AgentPublicationPolicyEvaluator()
        self._content_policy = content_policy or PublicationContentPolicy(
            archive_policy=archive_policy
        )
        self._archive_policy = archive_policy or ArchivePolicy()

    def authorize(
        self,
        *,
        run_id: str,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
        work_item: PublicationWorkItem,
    ) -> PublicationPolicyDecision:
        _require_matching_references(
            run_id=run_id,
            candidate=candidate,
            report=report,
            principal=principal,
            work_item=work_item,
        )
        _require_run_open(work_item)
        decision = self._evaluator.evaluate(
            principal=principal,
            admission=work_item.admission,
            current_executing=work_item.current_executing,
            current_root=work_item.current_root,
            actor_authorized=work_item.actor_authorized,
            resource_accessible=work_item.resource_accessible,
            executing_agent_active=work_item.executing_agent_active,
            root_agent_active=work_item.root_agent_active,
        )
        if not decision.allowed:
            raise PublicationDenied(decision.reason_code or CodingReasonCode.POLICY_DENIED)
        _require_binding_in_scopes(run=work_item.run, decision=decision)
        from rhetorica_backend.domain.coding.publication_decision import missing_required_reviews

        missing = missing_required_reviews(work_item.run, report)
        if missing:
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail="Missing required reviews: " + ", ".join(missing),
            )
        _require_successful_mandatory_report(report)
        return decision

    def prepare(
        self,
        *,
        run_id: str,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
        work_item: PublicationWorkItem,
        candidate_payload: bytes,
        base_tree: dict[str, bytes],
        denied_paths: Sequence[str] = (),
    ) -> PreparedPublication:
        decision = self.authorize(
            run_id=run_id,
            candidate=candidate,
            report=report,
            principal=principal,
            work_item=work_item,
        )
        _require_artifact_digest(candidate, candidate_payload, policy=self._archive_policy)
        try:
            candidate_tree = tree_from_candidate_archive(
                candidate_payload, policy=self._archive_policy
            )
            publication_diff = diff_trees(
                base=base_tree,
                candidate=candidate_tree,
                changed_paths=candidate.changed_paths,
            )
            self._content_policy.validate(
                publication_diff.changes,
                changed_paths=candidate.changed_paths,
                scopes=decision.effective_scopes,
                unexpected_paths=publication_diff.unexpected_paths,
                denied_paths=denied_paths,
            )
        except UnsafeArchiveError as exc:
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail=str(exc),
            ) from exc
        run = work_item.run
        return PreparedPublication(
            run_id=run.id,
            branch_name=publication_branch_name(run.id),
            commit_message=commit_message(
                run_id=run.id,
                candidate_digest=candidate.digest,
                report_digest=report.report_digest,
                base_sha=run.repository.base_sha,
            ),
            title=draft_mr_title(run.id),
            description_without_commit=draft_mr_description(
                run_id=run.id,
                candidate=candidate,
                report=report,
                commit_sha="pending",
            ),
            actions=publication_diff.changes,
            tree_digest=publication_diff.tree_digest,
            candidate_digest=candidate.digest,
            validation_report_digest=report.report_digest,
            base_sha=run.repository.base_sha,
            project_id=run.repository.project_id,
            target_branch=run.repository.target_branch,
            connector_id=run.repository.gitlab_connector_id,
            policy=decision,
        )


def mr_description_for(
    *,
    candidate: CandidateManifest,
    report: ValidationReport,
    commit_sha: str,
) -> str:
    return draft_mr_description(
        run_id=candidate.run_id,
        candidate=candidate,
        report=report,
        commit_sha=commit_sha,
    )


def _require_matching_references(
    *,
    run_id: str,
    candidate: CandidateManifest,
    report: ValidationReport,
    principal: ExecutionPrincipal,
    work_item: PublicationWorkItem,
) -> None:
    run = work_item.run
    if principal.run_id != run_id or run.id != run_id:
        raise PublicationDenied(CodingReasonCode.POLICY_DENIED, detail="publication_run_mismatch")
    if (
        principal.workspace_id != run.workspace_id
        or principal.actor_user_id != run.owner_user_id
        or candidate.workspace_id != run.workspace_id
        or candidate.owner_user_id != run.owner_user_id
        or candidate.run_id != run.id
        or report.workspace_id != run.workspace_id
        or report.run_id != run.id
    ):
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_ownership_mismatch"
        )
    if candidate.base_sha != run.repository.base_sha:
        raise PublicationDenied(CodingReasonCode.BASE_CHANGED, detail="publication_base_mismatch")
    if report.candidate_digest != candidate.digest:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_digest_mismatch"
        )
    if run.candidate is None:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_not_persisted"
        )
    if run.candidate.digest != candidate.digest:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_digest_mismatch"
        )
    if run.candidate != candidate:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_manifest_mismatch"
        )
    persisted = work_item.validation_report
    if (
        persisted is None
        or run.validation_report_id is None
        or persisted.id != run.validation_report_id
    ):
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_report_not_persisted"
        )
    if persisted != report:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_report_mismatch"
        )


def _require_artifact_digest(
    candidate: CandidateManifest, payload: bytes, *, policy: ArchivePolicy
) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, candidate.digest):
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_digest_mismatch"
        )
    if candidate.size_bytes > policy.max_total_bytes:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_candidate_oversize"
        )


def _require_run_open(work_item: PublicationWorkItem) -> None:
    if work_item.cancelled or work_item.run.status in STOPPED_STATUSES:
        raise PublicationDenied(CodingReasonCode.POLICY_DENIED, detail="publication_cancelled")
    if work_item.run.reason_code in BLOCKING_RUN_CODES:
        raise PublicationDenied(work_item.run.reason_code)


def _require_binding_in_scopes(*, run: CodingRun, decision: PublicationPolicyDecision) -> None:
    binding = run.repository
    for scope in decision.effective_scopes:
        if (
            scope.connector_id == binding.gitlab_connector_id
            and scope.project_id == binding.project_id
            and binding.target_branch in scope.target_branches
        ):
            return
    raise PublicationDenied(
        CodingReasonCode.PUBLICATION_NOT_AUTHORIZED,
        detail="publication_binding_not_in_scope",
    )


def _require_successful_mandatory_report(report: ValidationReport) -> None:
    mandatory = [check for check in report.checks if check.mandatory]
    if not mandatory:
        raise PublicationDenied(
            CodingReasonCode.POLICY_DENIED, detail="publication_mandatory_report_missing"
        )
    for check in mandatory:
        if check.status == ValidationStatus.PASSED:
            continue
        if check.status == ValidationStatus.NOT_RUN and check.name in {
            "dbt_materialization",
            "materialization",
        }:
            continue
        if check.status == ValidationStatus.NOT_RUN:
            detail = f"publication_mandatory_not_run:{check.name}"
        else:
            detail = f"publication_mandatory_failed:{check.name}"
        raise PublicationDenied(CodingReasonCode.POLICY_DENIED, detail=detail)
