"""GitLab adapter implementing PublicationPort with fenced reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.contracts.coding import (
    CandidateManifest,
    CodingReasonCode,
    ExecutionPrincipal,
    PublicationReceipt,
    ValidationReport,
)
from rhetorica_backend.domain.chat.gitlab import (
    WRITE_TOKEN_SCOPES,
    GitLabRequestError,
    GitLabService,
)
from rhetorica_backend.domain.coding.ports import ArtifactPort, PublicationPort
from rhetorica_backend.domain.coding.publication import (
    PreparedPublication,
    PublicationLedgerPort,
    PublicationPreparer,
    PublicationWorkItem,
    PublicationWorkItemPort,
    mr_description_for,
)
from rhetorica_backend.domain.coding.publication_content_policy import PublicationDenied
from rhetorica_backend.domain.coding.publication_diff import tree_from_gitlab_archive
from rhetorica_backend.domain.coding.publication_mr import trailers_match
from rhetorica_backend.infra.coding.archives import ArchivePolicy, UnsafeArchiveError

MAX_LEDGER_STEPS = 6
MAX_RECEIPT_DETAIL_CHARS = 512
AUTH_FAILURE_STATUSES = {401, 403}
REJECTED_REQUEST_STATUSES = {400, 404, 409, 422}


class GitLabConnectionResolver(Protocol):
    def get_connection_by_id(
        self, *, workspace_id: str, connector_id: str
    ) -> GitLabConnectionConfig | None: ...


class GitLabPublicationService(PublicationPort):
    """Publishes one Draft MR per run.

    Policy denials raise PublicationDenied; once a ledger row exists the exception
    carries the persisted receipt with reason_code/detail. Transport failures and
    GitLab 5xx propagate unchanged and are safe to retry with the same inputs.
    """

    def __init__(
        self,
        *,
        work_items: PublicationWorkItemPort,
        artifacts: ArtifactPort,
        ledger: PublicationLedgerPort,
        connectors: GitLabConnectionResolver,
        gitlab: GitLabService | None = None,
        preparer: PublicationPreparer | None = None,
        archive_policy: ArchivePolicy | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._work_items = work_items
        self._artifacts = artifacts
        self._ledger = ledger
        self._connectors = connectors
        self._gitlab = gitlab or GitLabService()
        self._archive_policy = archive_policy or ArchivePolicy()
        self._preparer = preparer or PublicationPreparer(archive_policy=self._archive_policy)
        self._now = now_fn or (lambda: datetime.now(tz=UTC))

    def publish(
        self,
        *,
        run_id: str,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
    ) -> PublicationReceipt:
        existing = self._ledger.get(
            workspace_id=principal.workspace_id,
            run_id=run_id,
            owner_user_id=principal.actor_user_id,
        )
        if existing is not None:
            same = _same_publication(existing, candidate=candidate, report=report)
            if not same and existing.state != "reserved":
                raise _ledger_mismatch(existing)
            if same and existing.state == "mr_recorded":
                return existing
            if existing.state == "reconcile_required":
                raise PublicationDenied(
                    CodingReasonCode.POLICY_DENIED,
                    detail="publication_conflict",
                    receipt=existing,
                )
        try:
            prepared, connection = self._prepare(
                run_id=run_id,
                candidate=candidate,
                report=report,
                principal=principal,
                check_target_base=existing is None or existing.state == "reserved",
            )
        except PublicationDenied as exc:
            raise self._denied(exc, existing) from exc
        if existing is None:
            receipt = self._ledger.reserve(
                PublicationReceipt(
                    id=str(uuid4()),
                    workspace_id=principal.workspace_id,
                    owner_user_id=principal.actor_user_id,
                    run_id=run_id,
                    candidate_digest=prepared.candidate_digest,
                    validation_report_digest=prepared.validation_report_digest,
                    state="reserved",
                    branch_name=prepared.branch_name,
                    created_at=self._now(),
                    updated_at=self._now(),
                )
            )
        elif not _same_publication(existing, candidate=candidate, report=report):
            receipt = self._replace_reserved(existing, prepared=prepared, connection=connection)
        else:
            receipt = existing
        return self._advance(
            receipt,
            prepared=prepared,
            connection=connection,
            candidate=candidate,
            report=report,
            principal=principal,
        )

    def _advance(
        self,
        receipt: PublicationReceipt,
        *,
        prepared: PreparedPublication,
        connection: GitLabConnectionConfig,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
    ) -> PublicationReceipt:
        for _ in range(MAX_LEDGER_STEPS):
            if (
                receipt.candidate_digest != prepared.candidate_digest
                or receipt.validation_report_digest != prepared.validation_report_digest
            ):
                raise _ledger_mismatch(receipt)
            if receipt.state == "mr_recorded":
                return receipt
            if receipt.state == "reconcile_required":
                raise PublicationDenied(
                    CodingReasonCode.POLICY_DENIED,
                    detail="publication_conflict",
                    receipt=receipt,
                )
            try:
                connection = self._reauthorize(
                    prepared=prepared,
                    connection=connection,
                    candidate=candidate,
                    report=report,
                    principal=principal,
                    check_target_base=receipt.state == "reserved",
                )
            except PublicationDenied as exc:
                raise self._denied(exc, receipt) from exc
            if receipt.state == "reserved":
                receipt = self._ensure_commit(
                    receipt=receipt, prepared=prepared, connection=connection
                )
            else:
                receipt = self._ensure_merge_request(
                    receipt=receipt,
                    prepared=prepared,
                    connection=connection,
                    candidate=candidate,
                    report=report,
                )
        raise RuntimeError("publication_ledger_contention")

    def _prepare(
        self,
        *,
        run_id: str,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
        check_target_base: bool,
    ) -> tuple[PreparedPublication, GitLabConnectionConfig]:
        work_item = self._work_items.load(run_id=run_id, principal=principal)
        self._preparer.authorize(
            run_id=run_id,
            candidate=candidate,
            report=report,
            principal=principal,
            work_item=work_item,
        )
        connection = self._connection_for(work_item)
        self._require_token_scope(connection)
        repository = work_item.run.repository
        denied_paths = self._ci_config_paths(connection, repository.project_id)
        candidate_payload = self._read_candidate(candidate)
        base_tree = tree_from_gitlab_archive(
            self._gitlab.download_repository_archive(
                connection,
                project_path=repository.project_id,
                commit_sha=repository.base_sha,
                max_bytes=self._archive_policy.max_total_bytes,
            ),
            policy=self._archive_policy,
        )
        prepared = self._preparer.prepare(
            run_id=run_id,
            candidate=candidate,
            report=report,
            principal=principal,
            work_item=work_item,
            candidate_payload=candidate_payload,
            base_tree=base_tree,
            denied_paths=denied_paths,
        )
        if check_target_base:
            self._require_target_base(connection, prepared)
        return prepared, connection

    def _reauthorize(
        self,
        *,
        prepared: PreparedPublication,
        connection: GitLabConnectionConfig,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
        check_target_base: bool,
    ) -> GitLabConnectionConfig:
        work_item = self._work_items.load(run_id=prepared.run_id, principal=principal)
        repository = work_item.run.repository
        if (
            repository.gitlab_connector_id != prepared.connector_id
            or repository.project_id != prepared.project_id
            or repository.target_branch != prepared.target_branch
            or repository.base_sha != prepared.base_sha
        ):
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED, detail="publication_binding_changed"
            )
        self._preparer.authorize(
            run_id=prepared.run_id,
            candidate=candidate,
            report=report,
            principal=principal,
            work_item=work_item,
        )
        current = self._connection_for(work_item)
        if current.base_url.rstrip("/") != connection.base_url.rstrip("/"):
            raise PublicationDenied(
                CodingReasonCode.RESOURCE_ACCESS_REVOKED,
                detail="publication_connector_endpoint_changed",
            )
        self._require_token_scope(current)
        if check_target_base:
            self._require_target_base(current, prepared)
        return current

    def _ensure_commit(
        self,
        *,
        receipt: PublicationReceipt,
        prepared: PreparedPublication,
        connection: GitLabConnectionConfig,
    ) -> PublicationReceipt:
        matched = self._matching_remote_commit(connection, prepared)
        if matched is not None:
            return self._record(receipt, state="commit_recorded", commit_sha=matched)
        try:
            payload = self._gitlab.create_commit(
                connection,
                project_path=prepared.project_id,
                branch=prepared.branch_name,
                start_sha=prepared.base_sha,
                commit_message=prepared.commit_message,
                actions=[change.gitlab_action() for change in prepared.actions],
            )
        except GitLabRequestError as exc:
            if exc.status_code in AUTH_FAILURE_STATUSES:
                raise self._denied(
                    PublicationDenied(CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT),
                    receipt,
                ) from exc
            matched = self._matching_remote_commit(connection, prepared)
            if matched is not None:
                return self._record(receipt, state="commit_recorded", commit_sha=matched)
            if exc.status_code in REJECTED_REQUEST_STATUSES:
                if self._branch_exists(connection, prepared):
                    raise PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail="publication_conflict",
                        receipt=self._conflict(receipt),
                    ) from exc
                raise self._denied(
                    PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail=_rejected_detail("publication_commit_rejected", exc),
                    ),
                    receipt,
                ) from exc
            raise
        commit_sha = str(payload.get("id") or "")
        if not commit_sha:
            matched = self._matching_remote_commit(connection, prepared)
            if matched is None:
                raise self._denied(
                    PublicationDenied(
                        CodingReasonCode.POLICY_DENIED, detail="publication_commit_unconfirmed"
                    ),
                    receipt,
                )
            commit_sha = matched
        return self._record(receipt, state="commit_recorded", commit_sha=commit_sha)

    def _ensure_merge_request(
        self,
        *,
        receipt: PublicationReceipt,
        prepared: PreparedPublication,
        connection: GitLabConnectionConfig,
        candidate: CandidateManifest,
        report: ValidationReport,
    ) -> PublicationReceipt:
        if not self._source_commit_matches(connection, prepared, receipt.commit_sha):
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail="publication_conflict",
                receipt=self._conflict(receipt),
            )
        existing = self._matching_merge_request(connection, prepared)
        if existing is not None:
            return self._record(receipt, state="mr_recorded", merge_request_url=existing)
        try:
            self._require_target_base(connection, prepared)
        except PublicationDenied as exc:
            raise self._denied(exc, receipt) from exc
        description = mr_description_for(
            candidate=candidate,
            report=report,
            commit_sha=receipt.commit_sha or "",
        )
        try:
            payload = self._gitlab.create_draft_merge_request(
                connection,
                project_path=prepared.project_id,
                source_branch=prepared.branch_name,
                target_branch=prepared.target_branch,
                title=prepared.title,
                description=description,
            )
        except GitLabRequestError as exc:
            if exc.status_code in AUTH_FAILURE_STATUSES:
                raise self._denied(
                    PublicationDenied(CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT),
                    receipt,
                ) from exc
            existing = self._matching_merge_request(connection, prepared)
            if existing is not None:
                return self._record(receipt, state="mr_recorded", merge_request_url=existing)
            if exc.status_code in REJECTED_REQUEST_STATUSES:
                raise self._denied(
                    PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail=_rejected_detail("publication_merge_request_rejected", exc),
                    ),
                    receipt,
                ) from exc
            raise
        url = str(payload.get("web_url") or payload.get("url") or "")
        if not url:
            existing = self._matching_merge_request(connection, prepared)
            if existing is None:
                raise self._denied(
                    PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail="publication_merge_request_unconfirmed",
                    ),
                    receipt,
                )
            url = existing
        return self._record(receipt, state="mr_recorded", merge_request_url=url)

    def _source_commit_matches(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
        commit_sha: str | None,
    ) -> bool:
        expected = (commit_sha or "").strip()
        if not expected:
            return False
        branch = self._gitlab.get_branch(
            connection,
            project_path=prepared.project_id,
            branch=prepared.branch_name,
        )
        if branch is None:
            return False
        commit = branch.get("commit") if isinstance(branch.get("commit"), dict) else {}
        head = str(commit.get("id") or branch.get("commit_id") or "")
        return bool(head) and head.lower() == expected.lower()

    def _replace_reserved(
        self,
        receipt: PublicationReceipt,
        *,
        prepared: PreparedPublication,
        connection: GitLabConnectionConfig,
    ) -> PublicationReceipt:
        if receipt.state != "reserved" or self._branch_exists(connection, prepared):
            raise _ledger_mismatch(receipt)
        replaced = receipt.model_copy(
            update={
                "candidate_digest": prepared.candidate_digest,
                "validation_report_digest": prepared.validation_report_digest,
                "reason_code": None,
                "detail": None,
                "updated_at": self._now(),
            }
        )
        saved = self._ledger.save(replaced, expected_state="reserved")
        return saved or self._reload(receipt)

    def _matching_remote_commit(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
    ) -> str | None:
        branch = self._gitlab.get_branch(
            connection,
            project_path=prepared.project_id,
            branch=prepared.branch_name,
        )
        if branch is None:
            return None
        commit_payload = branch.get("commit") if isinstance(branch.get("commit"), dict) else branch
        sha = str(commit_payload.get("id") or "")
        if not sha:
            return None
        commit = self._gitlab.get_commit(connection, project_path=prepared.project_id, sha=sha)
        parents = commit.get("parent_ids") or []
        parent = parents[0] if parents else ""
        if parent != prepared.base_sha:
            return None
        if not trailers_match(
            str(commit.get("message") or ""),
            run_id=prepared.run_id,
            candidate_digest=prepared.candidate_digest,
            report_digest=prepared.validation_report_digest,
            base_sha=prepared.base_sha,
        ):
            return None
        if not self._tree_matches(connection, prepared, sha):
            return None
        return sha

    def _tree_matches(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
        sha: str,
    ) -> bool:
        if not self._diff_paths_match(connection, prepared, sha):
            return False
        return self._remote_content_matches(connection, prepared, sha)

    def _diff_paths_match(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
        sha: str,
    ) -> bool:
        try:
            diff = self._gitlab.get_commit_diff(
                connection, project_path=prepared.project_id, sha=sha
            )
        except ValueError:
            return False
        expected: set[tuple[str, str]] = set()
        for change in prepared.actions:
            if change.action == "move" and change.previous_path:
                expected.add(("rename", change.file_path))
                expected.add(("rename-from", change.previous_path))
            elif change.action == "delete":
                expected.add(("delete", change.file_path))
            else:
                expected.add((change.action, change.file_path))
        observed: set[tuple[str, str]] = set()
        for item in diff:
            new_path = str(item.get("new_path") or item.get("new_file_path") or "")
            old_path = str(item.get("old_path") or item.get("old_file_path") or "")
            if item.get("deleted_file"):
                observed.add(("delete", old_path or new_path))
            elif item.get("renamed_file"):
                observed.add(("rename", new_path))
                observed.add(("rename-from", old_path))
            elif item.get("new_file"):
                observed.add(("create", new_path))
            else:
                observed.add(("update", new_path or old_path))
        return expected == observed

    def _remote_content_matches(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
        sha: str,
    ) -> bool:
        try:
            remote_tree = tree_from_gitlab_archive(
                self._gitlab.download_repository_archive(
                    connection,
                    project_path=prepared.project_id,
                    commit_sha=sha,
                    max_bytes=self._archive_policy.max_total_bytes,
                ),
                policy=self._archive_policy,
            )
        except GitLabRequestError as exc:
            if exc.status_code == 404:
                return False
            raise
        except (ValueError, UnsafeArchiveError):
            return False
        for change in prepared.actions:
            if change.action == "delete":
                if change.file_path in remote_tree:
                    return False
                continue
            expected = change.content_bytes or b""
            actual = remote_tree.get(change.file_path)
            if actual is None or not hmac.compare_digest(actual, expected):
                return False
            if (
                change.action == "move"
                and change.previous_path
                and change.previous_path in remote_tree
            ):
                return False
        return True

    def _matching_merge_request(
        self,
        connection: GitLabConnectionConfig,
        prepared: PreparedPublication,
    ) -> str | None:
        for item in self._gitlab.list_merge_requests(
            connection,
            project_path=prepared.project_id,
            source_branch=prepared.branch_name,
            target_branch=prepared.target_branch,
        ):
            url = str(item.get("web_url") or item.get("url") or "")
            draft = bool(item.get("draft") or item.get("work_in_progress"))
            title = str(item.get("title") or "")
            if url and (draft or title.lower().startswith("draft:")):
                return url
        return None

    def _branch_exists(
        self, connection: GitLabConnectionConfig, prepared: PreparedPublication
    ) -> bool:
        return (
            self._gitlab.get_branch(
                connection,
                project_path=prepared.project_id,
                branch=prepared.branch_name,
            )
            is not None
        )

    def _connection_for(self, work_item: PublicationWorkItem) -> GitLabConnectionConfig:
        connection = self._connectors.get_connection_by_id(
            workspace_id=work_item.run.workspace_id,
            connector_id=work_item.run.repository.gitlab_connector_id,
        )
        if connection is None:
            raise PublicationDenied(CodingReasonCode.RESOURCE_ACCESS_REVOKED)
        return connection

    def _ci_config_paths(
        self, connection: GitLabConnectionConfig, project_id: str
    ) -> tuple[str, ...]:
        project = self._gitlab.get_project(connection, project_path=project_id)
        value = str(project.get("ci_config_path") or "").strip().strip("/")
        # `file@group/project` and URLs point outside the repository being published.
        if not value or "@" in value or "://" in value:
            return ()
        return (value,)

    def _require_token_scope(self, connection: GitLabConnectionConfig) -> None:
        try:
            info = self._gitlab.get_access_token_info(connection)
        except GitLabRequestError as exc:
            if exc.status_code in AUTH_FAILURE_STATUSES:
                raise PublicationDenied(
                    CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT
                ) from exc
            raise
        if info is None:
            return
        scopes = {str(item) for item in info.get("scopes") or []}
        if not scopes.intersection(WRITE_TOKEN_SCOPES):
            raise PublicationDenied(CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT)

    def _require_target_base(
        self, connection: GitLabConnectionConfig, prepared: PreparedPublication
    ) -> None:
        branch = self._gitlab.get_branch(
            connection,
            project_path=prepared.project_id,
            branch=prepared.target_branch,
        )
        if branch is None:
            raise PublicationDenied(CodingReasonCode.BASE_CHANGED, detail="target_branch_missing")
        commit = branch.get("commit") if isinstance(branch.get("commit"), dict) else {}
        head = str(commit.get("id") or branch.get("commit_id") or "")
        if head.lower() != prepared.base_sha.lower():
            raise PublicationDenied(CodingReasonCode.BASE_CHANGED)

    def _read_candidate(self, candidate: CandidateManifest) -> bytes:
        if not self._artifacts.verify_owned(
            artifact_id=candidate.artifact_id,
            workspace_id=candidate.workspace_id,
            owner_user_id=candidate.owner_user_id,
        ):
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED, detail="publication_artifact_not_owned"
            )
        with self._artifacts.open_owned(
            artifact_id=candidate.artifact_id,
            workspace_id=candidate.workspace_id,
            owner_user_id=candidate.owner_user_id,
        ) as handle:
            payload = handle.read()
        digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(digest, candidate.digest):
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail="publication_candidate_digest_mismatch",
            )
        return payload

    def _record(
        self,
        receipt: PublicationReceipt,
        *,
        state: str,
        commit_sha: str | None = None,
        merge_request_url: str | None = None,
    ) -> PublicationReceipt:
        updated = receipt.model_copy(
            update={
                "state": state,
                "commit_sha": commit_sha if commit_sha is not None else receipt.commit_sha,
                "merge_request_url": (
                    merge_request_url
                    if merge_request_url is not None
                    else receipt.merge_request_url
                ),
                "reason_code": None,
                "detail": None,
                "updated_at": self._now(),
            }
        )
        saved = self._ledger.save(updated, expected_state=receipt.state)
        return saved or self._reload(receipt)

    def _conflict(self, receipt: PublicationReceipt) -> PublicationReceipt:
        updated = receipt.model_copy(
            update={
                "state": "reconcile_required",
                "reason_code": CodingReasonCode.POLICY_DENIED,
                "detail": "publication_conflict",
                "updated_at": self._now(),
            }
        )
        return self._ledger.save(updated, expected_state=receipt.state) or self._reload(receipt)

    def _denied(
        self, exc: PublicationDenied, receipt: PublicationReceipt | None
    ) -> PublicationDenied:
        if receipt is None:
            return exc
        detail = exc.detail[:MAX_RECEIPT_DETAIL_CHARS] if exc.detail else None
        updated = receipt.model_copy(
            update={
                "reason_code": exc.reason_code,
                "detail": detail,
                "updated_at": self._now(),
            }
        )
        saved = self._ledger.save(updated, expected_state=receipt.state)
        return PublicationDenied(
            exc.reason_code,
            detail=exc.detail,
            receipt=saved or self._reload(receipt),
        )

    def _reload(self, receipt: PublicationReceipt) -> PublicationReceipt:
        current = self._ledger.get(
            workspace_id=receipt.workspace_id,
            run_id=receipt.run_id,
            owner_user_id=receipt.owner_user_id,
        )
        if current is None:
            raise RuntimeError("publication_ledger_row_missing")
        return current


def _same_publication(
    receipt: PublicationReceipt, *, candidate: CandidateManifest, report: ValidationReport
) -> bool:
    return (
        receipt.candidate_digest == candidate.digest
        and receipt.validation_report_digest == report.report_digest
    )


def _ledger_mismatch(receipt: PublicationReceipt) -> PublicationDenied:
    return PublicationDenied(
        CodingReasonCode.POLICY_DENIED,
        detail="publication_ledger_candidate_mismatch",
        receipt=receipt,
    )


def _rejected_detail(prefix: str, exc: GitLabRequestError) -> str:
    try:
        body = json.loads(exc.response_body)
    except ValueError:
        body = exc.response_body
    if isinstance(body, dict):
        body = body.get("message") or body.get("error") or ""
    if isinstance(body, list | dict):
        body = json.dumps(body, sort_keys=True)
    message = " ".join(str(body).split())
    detail = f"{prefix}:{exc.status_code}" + (f":{message}" if message else "")
    return detail[:MAX_RECEIPT_DETAIL_CHARS]
