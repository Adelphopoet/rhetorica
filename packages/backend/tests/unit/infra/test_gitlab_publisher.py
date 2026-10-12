from __future__ import annotations

import gzip
import hashlib
import inspect
import io
import json
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.contracts.coding import (
    AgentDelegationGrant,
    AgentPolicySnapshot,
    CandidateManifest,
    CodingReasonCode,
    CodingRun,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    PublicationReceipt,
    ValidationCheck,
    ValidationReport,
)
from rhetorica_backend.domain.chat.gitlab import GitLabRequestError, GitLabService
from rhetorica_backend.domain.coding.publication import PublicationWorkItem
from rhetorica_backend.domain.coding.publication_content_policy import PublicationDenied
from rhetorica_backend.domain.coding.publication_diff import tree_from_gitlab_archive
from rhetorica_backend.domain.coding.publication_mr import commit_message
from rhetorica_backend.infra.coding.gitlab_publisher import GitLabPublicationService

BASE_SHA = "b" * 40
RUN_ID = "run-success"
BRANCH = "feature/rhetorica/run-success"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "coding"


def _tar(files: dict[str, str], *, prefix: str = "") -> bytes:
    output = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path, text in sorted(files.items()):
            name = f"{prefix}{path}" if prefix else path
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def _tar_from_bytes(files: dict[str, bytes], *, prefix: str = "analytics-dbt/") -> bytes:
    output = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path, payload in sorted(files.items()):
            name = f"{prefix}{path}" if prefix else path
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


class MemoryArtifacts:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def verify_owned(self, *, artifact_id: str, workspace_id: str, owner_user_id: str) -> bool:
        return artifact_id in self.blobs

    def open_owned(self, *, artifact_id: str, workspace_id: str, owner_user_id: str):
        return io.BytesIO(self.blobs[artifact_id])


class InMemoryLedger:
    """Mirrors the Postgres ledger: compare-and-set on state and attempt."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], PublicationReceipt] = {}
        self._lock = threading.Lock()

    def get(
        self, *, workspace_id: str, run_id: str, owner_user_id: str
    ) -> PublicationReceipt | None:
        with self._lock:
            row = self._rows.get((workspace_id, run_id))
        if row is None or row.owner_user_id != owner_user_id:
            return None
        return row

    def reserve(self, receipt: PublicationReceipt) -> PublicationReceipt:
        key = (receipt.workspace_id, receipt.run_id)
        with self._lock:
            existing = self._rows.get(key)
            if existing is not None:
                return existing
            stored = receipt.model_copy(update={"attempt": 1})
            self._rows[key] = stored
            return stored

    def save(
        self, receipt: PublicationReceipt, *, expected_state: str
    ) -> PublicationReceipt | None:
        key = (receipt.workspace_id, receipt.run_id)
        with self._lock:
            current = self._rows.get(key)
            if (
                current is None
                or current.id != receipt.id
                or current.owner_user_id != receipt.owner_user_id
                or current.state != expected_state
                or current.attempt != receipt.attempt
            ):
                return None
            stored = receipt.model_copy(update={"attempt": current.attempt + 1})
            self._rows[key] = stored
            return stored


class FakeConnectors:
    def __init__(self, connection: GitLabConnectionConfig) -> None:
        self.connection = connection
        self.requested: list[str] = []

    def get_connection_by_id(
        self, *, workspace_id: str, connector_id: str
    ) -> GitLabConnectionConfig | None:
        self.requested.append(connector_id)
        if connector_id != "gitlab-1":
            return None
        return self.connection


class FakeWorkItems:
    def __init__(self, item: PublicationWorkItem) -> None:
        self.item = item

    def load(self, *, run_id: str, principal: ExecutionPrincipal) -> PublicationWorkItem:
        assert run_id == self.item.run.id
        return self.item


class FakeGitLab:
    def __init__(self, *, base_archive: bytes, token_scopes: list[str] | None = None) -> None:
        self.base_archive = base_archive
        self.target_sha = BASE_SHA
        self.token_scopes = token_scopes if token_scopes is not None else ["api"]
        self.missing_token_info = False
        self.ci_config_path: str | None = None
        self.max_diff_page_size = 20
        self.requests: list[tuple[str, str]] = []
        self.branches: dict[str, dict[str, object]] = {}
        self.diffs: dict[str, list[dict[str, object]]] = {}
        self.merge_requests: list[dict[str, object]] = []
        self.commit_posts = 0
        self.mr_posts = 0
        self.archive_downloads = 0
        self.hide_mr_once = False
        self.fail_commit_response = False
        self.fail_mr_response = False
        self.feature_visible = True
        self.force_commit_forbidden = False
        self.reject_commit_message: str | None = None
        self.base_tree = tree_from_gitlab_archive(base_archive)
        self.commit_archives: dict[str, bytes] = {BASE_SHA: base_archive}
        self._lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if request.method not in {"GET", "POST"}:
            return httpx.Response(405, text="method not allowed")
        if any(
            fragment in path
            for fragment in ("/protected_branches", "/jobs", "/pipelines", "/triggers")
        ) or path.endswith("/merge"):
            return httpx.Response(403, text="forbidden write surface")
        if path.endswith("/personal_access_tokens/self"):
            if self.missing_token_info:
                return httpx.Response(404, text="missing")
            return httpx.Response(200, json={"scopes": self.token_scopes})
        if request.method == "GET" and path == "/api/v4/projects/analytics/dbt":
            return httpx.Response(
                200,
                json={
                    "path_with_namespace": "analytics/dbt",
                    "ci_config_path": self.ci_config_path,
                },
            )
        if path.endswith("/repository/archive.tar.gz"):
            self.archive_downloads += 1
            sha = str(request.url.params.get("sha") or BASE_SHA)
            archive = self.commit_archives.get(sha)
            if archive is None:
                return httpx.Response(404, text="missing archive")
            return httpx.Response(200, content=archive)
        if request.method == "GET" and "/repository/branches/" in path:
            name = unquote(path.rsplit("/repository/branches/", 1)[1])
            if name == "main":
                return httpx.Response(200, json={"name": "main", "commit": {"id": self.target_sha}})
            if not self.feature_visible:
                return httpx.Response(404, text="not ready")
            branch = self.branches.get(name)
            if branch is None:
                return httpx.Response(404, text="missing")
            return httpx.Response(200, json=branch)
        if request.method == "GET" and path.endswith("/diff"):
            sha = unquote(path.rsplit("/repository/commits/", 1)[1].removesuffix("/diff"))
            return self._diff_page(request, self.diffs.get(sha, []))
        if request.method == "GET" and "/repository/commits/" in path:
            sha = unquote(path.rsplit("/repository/commits/", 1)[1])
            branch = next(
                (item for item in self.branches.values() if item["commit"]["id"] == sha), None
            )
            if branch is None:
                return httpx.Response(404, text="missing commit")
            return httpx.Response(200, json=branch["commit"])
        if request.method == "GET" and path.endswith("/merge_requests"):
            if self.hide_mr_once:
                self.hide_mr_once = False
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=self.merge_requests)
        if request.method == "POST" and path.endswith("/repository/commits"):
            return self._create_commit(json.loads(request.content.decode()))
        if request.method == "POST" and path.endswith("/merge_requests"):
            return self._create_mr(json.loads(request.content.decode()))
        return httpx.Response(500, text=path)

    def _diff_page(
        self, request: httpx.Request, entries: list[dict[str, object]]
    ) -> httpx.Response:
        per_page = min(int(request.url.params.get("per_page", "20")), self.max_diff_page_size)
        page = int(request.url.params.get("page", "1"))
        start = (page - 1) * per_page
        chunk = entries[start : start + per_page]
        next_page = str(page + 1) if start + per_page < len(entries) else ""
        return httpx.Response(
            200,
            json=chunk,
            headers={"x-per-page": str(per_page), "x-page": str(page), "x-next-page": next_page},
        )

    def _create_commit(self, body: dict[str, object]) -> httpx.Response:
        self.commit_posts += 1
        branch_name = str(body["branch"])
        if branch_name in self.branches:
            return httpx.Response(400, json={"message": "A branch called this already exists"})
        scopes = set(self.token_scopes)
        if self.force_commit_forbidden or "api" not in scopes:
            return httpx.Response(403, json={"error": "insufficient_scope"})
        if self.reject_commit_message is not None:
            return httpx.Response(400, json={"message": self.reject_commit_message})
        sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        commit = {
            "id": sha,
            "parent_ids": [str(body["start_sha"])],
            "message": str(body["commit_message"]),
        }
        self.branches[branch_name] = {"name": branch_name, "commit": commit}
        self.diffs[sha] = [_diff_entry(action) for action in body.get("actions", [])]
        self.commit_archives[sha] = _tar_from_bytes(
            self._tree_after_actions(body.get("actions", []))
        )
        if self.fail_commit_response:
            self.fail_commit_response = False
            return httpx.Response(504, text="lost commit")
        return httpx.Response(201, json=commit)

    def _create_mr(self, body: dict[str, object]) -> httpx.Response:
        self.mr_posts += 1
        assert body.get("draft") is True
        assert str(body.get("title", "")).lower().startswith("draft:")
        if any(
            item["source_branch"] == body["source_branch"]
            and item["target_branch"] == body["target_branch"]
            for item in self.merge_requests
        ):
            return httpx.Response(
                409,
                json={
                    "message": [
                        "Another open merge request already exists for this source branch: !1"
                    ]
                },
            )
        url = "https://gitlab.example.com/analytics/dbt/-/merge_requests/1"
        record = {
            "web_url": url,
            "draft": True,
            "title": body["title"],
            "description": body.get("description"),
            "source_branch": body["source_branch"],
            "target_branch": body["target_branch"],
        }
        self.merge_requests.append(record)
        if self.fail_mr_response:
            self.fail_mr_response = False
            self.hide_mr_once = True
            return httpx.Response(504, text="lost mr")
        return httpx.Response(201, json=record)

    def _tree_after_actions(self, actions: object) -> dict[str, bytes]:
        tree = dict(self.base_tree)
        if not isinstance(actions, list):
            return tree
        for action in actions:
            if not isinstance(action, dict):
                continue
            kind = str(action.get("action") or "")
            path = str(action.get("file_path") or "")
            if kind == "delete":
                tree.pop(path, None)
                continue
            payload = str(action.get("content") or "").encode()
            if kind == "move":
                previous = str(action.get("previous_path") or "")
                if not payload and previous in tree:
                    payload = tree[previous]
                tree.pop(previous, None)
            tree[path] = payload
        return tree


def _diff_entry(action: object) -> dict[str, object]:
    assert isinstance(action, dict)
    kind = str(action["action"])
    path = str(action["file_path"])
    if kind == "delete":
        return {"old_path": path, "new_path": path, "deleted_file": True}
    if kind == "move":
        return {
            "old_path": str(action["previous_path"]),
            "new_path": path,
            "renamed_file": True,
        }
    if kind == "create":
        return {"old_path": path, "new_path": path, "new_file": True}
    return {"old_path": path, "new_path": path, "new_file": False, "deleted_file": False}


def _connection() -> GitLabConnectionConfig:
    return GitLabConnectionConfig(
        provider="gitlab",
        base_url="https://gitlab.example.com",
        api_token="token",
        use_when="coding publication tests",
        default_project_paths=["analytics/dbt"],
    )


def _scope() -> GitLabWriteScope:
    return GitLabWriteScope(
        connector_id="gitlab-1",
        project_id="analytics/dbt",
        target_branches=["main"],
        allowed_paths=["models"],
    )


@dataclass(frozen=True)
class Bundle:
    base: bytes
    payload: bytes
    candidate: CandidateManifest
    report: ValidationReport
    principal: ExecutionPrincipal
    work_item: PublicationWorkItem

    def publish_args(self) -> dict[str, object]:
        return {
            "run_id": RUN_ID,
            "candidate": self.candidate,
            "report": self.report,
            "principal": self.principal,
        }


@dataclass(frozen=True)
class Harness:
    service: GitLabPublicationService
    ledger: InMemoryLedger
    connectors: FakeConnectors
    work_items: FakeWorkItems
    artifacts: MemoryArtifacts


def _bundle(
    base_files: dict[str, str] | None = None,
    candidate_files: dict[str, str] | None = None,
) -> Bundle:
    base = _tar(base_files or {"models/fct_orders.sql": "select 1"}, prefix="analytics-dbt/")
    candidate_payload = _tar(candidate_files or {"models/fct_orders.sql": "select net_revenue"})
    digest = hashlib.sha256(candidate_payload).hexdigest()
    candidate = CandidateManifest(
        artifact_id="candidate-1",
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id=RUN_ID,
        digest=digest,
        base_sha=BASE_SHA,
        changed_paths=sorted(candidate_files or {"models/fct_orders.sql": ""}),
        size_bytes=len(candidate_payload),
        created_at=datetime(2026, 9, 22, 9, tzinfo=UTC),
    )
    report = ValidationReport.model_validate_json(
        (FIXTURES / "not_run_report.json").read_text(encoding="utf-8")
    ).model_copy(update={"candidate_digest": digest})
    run = CodingRun.model_validate_json(
        (FIXTURES / "success.json").read_text(encoding="utf-8")
    ).model_copy(
        update={
            "phase": "publishing",
            "status": "running",
            "publication_receipt_id": None,
            "candidate": candidate,
            "validation_report_id": report.id,
        }
    )
    executing = GovernedAgentSettings(revision=2, gitlab_write_scopes=[_scope()])
    root = GovernedAgentSettings(
        revision=5,
        allowed_subagent_ids=["developer"],
        delegation_grants=[
            AgentDelegationGrant(subagent_id="developer", gitlab_write_scopes=[_scope()])
        ],
    )
    principal = ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        root_agent_id="manager",
        parent_agent_id="manager",
        executing_agent_id="developer",
        run_id=RUN_ID,
        task_id="task-1",
    )
    work_item = PublicationWorkItem(
        run=run,
        admission=AgentPolicySnapshot(
            workspace_id="analytics",
            root_agent_id="manager",
            executing_agent_id="developer",
            root_settings=root,
            executing_settings=executing,
            resource_connector_id="gitlab-1",
            run_scope=_scope(),
        ),
        current_executing=executing,
        current_root=root,
        actor_authorized=True,
        resource_accessible=True,
        executing_agent_active=True,
        root_agent_active=True,
        validation_report=report,
    )
    return Bundle(base, candidate_payload, candidate, report, principal, work_item)


def _variant(
    bundle: Bundle,
    files: dict[str, str],
    *,
    changed_paths: list[str] | None = None,
) -> Bundle:
    payload = _tar(files)
    digest = hashlib.sha256(payload).hexdigest()
    candidate = bundle.candidate.model_copy(
        update={
            "artifact_id": f"candidate-{digest[:12]}",
            "digest": digest,
            "size_bytes": len(payload),
            "changed_paths": changed_paths or sorted(files),
        }
    )
    report = bundle.report.model_copy(update={"candidate_digest": digest})
    run = bundle.work_item.run.model_copy(update={"candidate": candidate})
    work_item = replace(bundle.work_item, run=run, validation_report=report)
    return replace(bundle, payload=payload, candidate=candidate, report=report, work_item=work_item)


def _publisher(
    fake: FakeGitLab,
    bundle: Bundle,
    *,
    ledger: InMemoryLedger | None = None,
) -> Harness:
    transport = httpx.MockTransport(fake.handler)

    def client_factory(**kwargs) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    ledger = ledger or InMemoryLedger()
    connectors = FakeConnectors(_connection())
    work_items = FakeWorkItems(bundle.work_item)
    artifacts = MemoryArtifacts({bundle.candidate.artifact_id: bundle.payload})
    service = GitLabPublicationService(
        work_items=work_items,
        artifacts=artifacts,
        ledger=ledger,
        connectors=connectors,
        gitlab=GitLabService(client_factory=client_factory),
    )
    return Harness(service, ledger, connectors, work_items, artifacts)


def _stored(ledger: InMemoryLedger) -> PublicationReceipt | None:
    return ledger.get(workspace_id="analytics", run_id=RUN_ID, owner_user_id="user-1")


def _assert_fenced(requests: list[tuple[str, str]]) -> None:
    for method, path in requests:
        assert method in {"GET", "POST"}
        assert not path.endswith("/merge")
        assert "/protected_branches" not in path
        assert "/jobs" not in path
        assert "/pipelines" not in path
        assert "/triggers" not in path


def test_ac24_successful_draft_mr_with_not_run_limitation() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    receipt = harness.service.publish(**bundle.publish_args())
    assert receipt.state == "mr_recorded"
    assert receipt.branch_name == BRANCH
    assert receipt.merge_request_url is not None
    assert receipt.reason_code is None
    assert fake.commit_posts == 1
    assert fake.mr_posts == 1
    assert fake.archive_downloads == 1
    assert harness.connectors.requested == ["gitlab-1", "gitlab-1", "gitlab-1"]
    assert str(fake.merge_requests[0]["title"]).startswith("Draft:")
    description = str(fake.merge_requests[0]["description"])
    assert "not_run" in description
    assert "Read-only validation cannot materialize" in description
    assert "Raw DWH result exports are not attached." in description
    _assert_fenced(fake.requests)
    retry = harness.service.publish(**bundle.publish_args())
    assert retry.id == receipt.id
    assert fake.commit_posts == 1
    assert fake.mr_posts == 1
    assert fake.archive_downloads == 1


def test_ac21_lost_commit_and_mr_responses_reconcile_without_duplicates() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.fail_commit_response = True
    fake.feature_visible = False
    harness = _publisher(fake, bundle)
    with pytest.raises(GitLabRequestError):
        harness.service.publish(**bundle.publish_args())
    reserved = _stored(harness.ledger)
    assert reserved is not None
    assert reserved.state == "reserved"
    fake.feature_visible = True
    receipt = harness.service.publish(**bundle.publish_args())
    assert receipt.state == "mr_recorded"
    assert fake.commit_posts == 1

    fake2 = FakeGitLab(base_archive=bundle.base)
    fake2.branches = fake.branches
    fake2.diffs = fake.diffs
    fake2.commit_archives = fake.commit_archives
    harness2 = _publisher(fake2, bundle)
    fake2.fail_mr_response = True
    with pytest.raises(GitLabRequestError):
        harness2.service.publish(**bundle.publish_args())
    partial = _stored(harness2.ledger)
    assert partial is not None
    assert partial.state == "commit_recorded"
    assert partial.merge_request_url is None
    assert fake2.mr_posts == 1
    second = harness2.service.publish(**bundle.publish_args())
    assert second.state == "mr_recorded"
    assert fake2.mr_posts == 1
    assert _stored(harness2.ledger) == second


def test_ac21_lost_commit_response_reconciles_multi_page_diff() -> None:
    files = {f"models/m{index:02d}.sql": "select 1" for index in range(25)}
    bundle = _bundle(
        base_files=files,
        candidate_files=dict.fromkeys(files, "select 2"),
    )
    fake = FakeGitLab(base_archive=bundle.base)
    fake.max_diff_page_size = 10
    fake.fail_commit_response = True
    fake.feature_visible = False
    harness = _publisher(fake, bundle)
    with pytest.raises(GitLabRequestError):
        harness.service.publish(**bundle.publish_args())
    fake.feature_visible = True
    receipt = harness.service.publish(**bundle.publish_args())
    assert receipt.state == "mr_recorded"
    assert fake.commit_posts == 1
    diff_pages = [path for _, path in fake.requests if path.endswith("/diff")]
    assert len(diff_pages) >= 3


def test_ac20_changed_digest_or_failed_check_does_not_call_gitlab_write() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    other = bundle.candidate.model_copy(update={"digest": "0" * 64})
    with pytest.raises(PublicationDenied) as mismatch:
        harness.service.publish(**{**bundle.publish_args(), "candidate": other})
    assert mismatch.value.reason_code == CodingReasonCode.POLICY_DENIED
    failed = bundle.report.model_copy(
        update={"checks": [ValidationCheck(name="dbt_compile", status="failed", mandatory=True)]}
    )
    with pytest.raises(PublicationDenied, match="publication_report_mismatch"):
        harness.service.publish(**{**bundle.publish_args(), "report": failed})
    harness.work_items.item = replace(bundle.work_item, validation_report=failed)
    with pytest.raises(PublicationDenied, match="publication_mandatory_failed"):
        harness.service.publish(**{**bundle.publish_args(), "report": failed})
    assert fake.commit_posts == 0
    assert fake.mr_posts == 0
    assert _stored(harness.ledger) is None


def test_ac20_candidate_and_report_must_be_persisted_for_the_run() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    harness.work_items.item = replace(bundle.work_item, validation_report=None)
    with pytest.raises(PublicationDenied, match="publication_report_not_persisted"):
        harness.service.publish(**bundle.publish_args())
    other_report = bundle.report.model_copy(update={"id": "report-other"})
    harness.work_items.item = replace(bundle.work_item, validation_report=other_report)
    with pytest.raises(PublicationDenied, match="publication_report_not_persisted"):
        harness.service.publish(**bundle.publish_args())
    unpersisted_run = bundle.work_item.run.model_copy(update={"candidate": None})
    harness.work_items.item = replace(bundle.work_item, run=unpersisted_run)
    with pytest.raises(PublicationDenied, match="publication_candidate_not_persisted"):
        harness.service.publish(**bundle.publish_args())
    harness.work_items.item = bundle.work_item
    relabelled = bundle.candidate.model_copy(update={"changed_paths": ["models/other.sql"]})
    with pytest.raises(PublicationDenied, match="publication_candidate_manifest_mismatch"):
        harness.service.publish(**{**bundle.publish_args(), "candidate": relabelled})
    assert fake.commit_posts == 0
    assert fake.archive_downloads == 0


def test_ac22_base_change_and_branch_collision() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.target_sha = "d" * 40
    harness = _publisher(fake, bundle)
    with pytest.raises(PublicationDenied) as changed:
        harness.service.publish(**bundle.publish_args())
    assert changed.value.reason_code == CodingReasonCode.BASE_CHANGED
    assert changed.value.receipt is None
    assert fake.commit_posts == 0

    fake_collision = FakeGitLab(base_archive=bundle.base)
    fake_collision.branches[BRANCH] = {
        "name": BRANCH,
        "commit": {"id": "a" * 40, "parent_ids": [BASE_SHA], "message": "unrelated"},
    }
    collision = _publisher(fake_collision, bundle)
    with pytest.raises(PublicationDenied, match="publication_conflict") as conflict:
        collision.service.publish(**bundle.publish_args())
    assert conflict.value.receipt is not None
    assert conflict.value.receipt.state == "reconcile_required"
    assert conflict.value.receipt.detail == "publication_conflict"
    assert _stored(collision.ledger) == conflict.value.receipt


def test_ac21_foreign_commit_with_same_paths_and_other_content_requires_reconcile() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    sha = "a" * 40
    fake.branches[BRANCH] = {
        "name": BRANCH,
        "commit": {
            "id": sha,
            "parent_ids": [BASE_SHA],
            "message": commit_message(
                run_id=RUN_ID,
                candidate_digest=bundle.candidate.digest,
                report_digest=bundle.report.report_digest,
                base_sha=BASE_SHA,
            ),
        },
    }
    fake.diffs[sha] = [_diff_entry({"action": "update", "file_path": "models/fct_orders.sql"})]
    fake.commit_archives[sha] = _tar(
        {"models/fct_orders.sql": "select leaked_secret"}, prefix="analytics-dbt/"
    )
    harness = _publisher(fake, bundle)
    with pytest.raises(PublicationDenied, match="publication_conflict") as conflict:
        harness.service.publish(**bundle.publish_args())
    assert conflict.value.receipt is not None
    assert conflict.value.receipt.state == "reconcile_required"
    assert fake.mr_posts == 0
    assert _stored(harness.ledger) == conflict.value.receipt


def test_ac22_target_moved_after_commit_keeps_partial_receipt_without_mr() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    original_ensure_commit = harness.service._ensure_commit

    def commit_then_move_target(**kwargs):
        receipt = original_ensure_commit(**kwargs)
        fake.target_sha = "d" * 40
        return receipt

    harness.service._ensure_commit = commit_then_move_target  # type: ignore[method-assign]
    with pytest.raises(PublicationDenied) as changed:
        harness.service.publish(**bundle.publish_args())
    assert changed.value.reason_code == CodingReasonCode.BASE_CHANGED
    partial = changed.value.receipt
    assert isinstance(partial, PublicationReceipt)
    assert partial.state == "commit_recorded"
    assert partial.commit_sha
    assert partial.merge_request_url is None
    assert partial.reason_code == CodingReasonCode.BASE_CHANGED
    assert fake.mr_posts == 0
    assert _stored(harness.ledger) == partial


def test_ac22_existing_mr_is_reused_even_if_target_moved() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.fail_mr_response = True
    harness = _publisher(fake, bundle)
    with pytest.raises(GitLabRequestError):
        harness.service.publish(**bundle.publish_args())
    fake.target_sha = "d" * 40
    recovered = harness.service.publish(**bundle.publish_args())
    assert recovered.state == "mr_recorded"
    assert recovered.reason_code is None
    assert fake.mr_posts == 1


def test_ac21_source_branch_moved_after_commit_requires_reconcile() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    original_ensure_commit = harness.service._ensure_commit

    def commit_then_rewrite_source(**kwargs):
        receipt = original_ensure_commit(**kwargs)
        fake.branches[BRANCH] = {
            "name": BRANCH,
            "commit": {
                "id": "c" * 40,
                "parent_ids": [receipt.commit_sha],
                "message": "unrelated follow-up",
            },
        }
        return receipt

    harness.service._ensure_commit = commit_then_rewrite_source  # type: ignore[method-assign]
    with pytest.raises(PublicationDenied, match="publication_conflict") as conflict:
        harness.service.publish(**bundle.publish_args())
    assert conflict.value.receipt is not None
    assert conflict.value.receipt.state == "reconcile_required"
    assert conflict.value.receipt.commit_sha
    assert fake.mr_posts == 0
    assert _stored(harness.ledger) == conflict.value.receipt


def test_ac21_source_branch_moved_does_not_attach_existing_mr() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.fail_mr_response = True
    harness = _publisher(fake, bundle)
    with pytest.raises(GitLabRequestError):
        harness.service.publish(**bundle.publish_args())
    recorded = _stored(harness.ledger)
    assert recorded is not None
    assert recorded.state == "commit_recorded"
    fake.branches[BRANCH]["commit"] = {
        "id": "c" * 40,
        "parent_ids": [recorded.commit_sha],
        "message": "unrelated follow-up",
    }
    with pytest.raises(PublicationDenied, match="publication_conflict") as conflict:
        harness.service.publish(**bundle.publish_args())
    assert conflict.value.receipt is not None
    assert conflict.value.receipt.state == "reconcile_required"
    assert conflict.value.receipt.merge_request_url is None
    assert fake.mr_posts == 1


def test_ac22_revoke_between_commit_and_mr_returns_partial_receipt_with_reason() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    original_ensure_commit = harness.service._ensure_commit

    def commit_then_revoke(**kwargs):
        receipt = original_ensure_commit(**kwargs)
        harness.work_items.item = replace(bundle.work_item, resource_accessible=False)
        return receipt

    harness.service._ensure_commit = commit_then_revoke  # type: ignore[method-assign]
    with pytest.raises(PublicationDenied) as revoked:
        harness.service.publish(**bundle.publish_args())
    assert revoked.value.reason_code == CodingReasonCode.RESOURCE_ACCESS_REVOKED
    partial = revoked.value.receipt
    assert isinstance(partial, PublicationReceipt)
    assert partial.state == "commit_recorded"
    assert partial.commit_sha
    assert partial.merge_request_url is None
    assert partial.reason_code == CodingReasonCode.RESOURCE_ACCESS_REVOKED
    assert _stored(harness.ledger) == partial
    assert fake.mr_posts == 0

    with pytest.raises(PublicationDenied) as still_revoked:
        harness.service.publish(**bundle.publish_args())
    assert still_revoked.value.receipt is not None
    assert still_revoked.value.receipt.id == partial.id
    assert still_revoked.value.receipt.state == "commit_recorded"
    assert fake.mr_posts == 0

    harness.work_items.item = bundle.work_item
    recovered = harness.service.publish(**bundle.publish_args())
    assert recovered.state == "mr_recorded"
    assert recovered.reason_code is None
    assert recovered.commit_sha == partial.commit_sha
    assert fake.commit_posts == 1
    assert fake.mr_posts == 1


def test_commit_rejected_without_branch_keeps_reservation_with_gitlab_reason() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.reject_commit_message = "Commit message does not follow the pattern '^JIRA-\\d+'"
    harness = _publisher(fake, bundle)
    with pytest.raises(PublicationDenied) as rejected:
        harness.service.publish(**bundle.publish_args())
    assert rejected.value.reason_code == CodingReasonCode.POLICY_DENIED
    assert rejected.value.detail == (
        "publication_commit_rejected:400:Commit message does not follow the pattern '^JIRA-\\d+'"
    )
    receipt = rejected.value.receipt
    assert receipt is not None
    assert receipt.state == "reserved"
    assert receipt.detail == rejected.value.detail
    assert BRANCH not in fake.branches

    fake.reject_commit_message = None
    published = harness.service.publish(**bundle.publish_args())
    assert published.id == receipt.id
    assert published.state == "mr_recorded"
    assert published.reason_code is None
    assert published.detail is None


def test_reserved_row_accepts_new_candidate_only_while_branch_is_absent() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    fake.reject_commit_message = "push rule"
    harness = _publisher(fake, bundle)
    with pytest.raises(PublicationDenied):
        harness.service.publish(**bundle.publish_args())
    reserved = _stored(harness.ledger)
    assert reserved is not None
    assert reserved.state == "reserved"

    revised = _variant(bundle, {"models/fct_orders.sql": "select gross_revenue"})
    harness.work_items.item = revised.work_item
    harness.artifacts.blobs[revised.candidate.artifact_id] = revised.payload
    fake.reject_commit_message = None
    published = harness.service.publish(**revised.publish_args())
    assert published.id == reserved.id
    assert published.state == "mr_recorded"
    assert published.candidate_digest == revised.candidate.digest
    with pytest.raises(PublicationDenied, match="publication_ledger_candidate_mismatch"):
        harness.service.publish(**bundle.publish_args())

    lost = FakeGitLab(base_archive=bundle.base)
    lost.fail_commit_response = True
    lost.feature_visible = False
    lost_harness = _publisher(lost, bundle)
    with pytest.raises(GitLabRequestError):
        lost_harness.service.publish(**bundle.publish_args())
    lost.feature_visible = True
    lost_harness.work_items.item = revised.work_item
    lost_harness.artifacts.blobs[revised.candidate.artifact_id] = revised.payload
    with pytest.raises(PublicationDenied, match="publication_ledger_candidate_mismatch"):
        lost_harness.service.publish(**revised.publish_args())
    assert lost.commit_posts == 1


def test_ac23_forbidden_content_and_read_only_token_keep_connector() -> None:
    bundle = _bundle()
    secret = _variant(bundle, {"models/fct_orders.sql": "token = glpat-" + ("a" * 24)})
    fake = FakeGitLab(base_archive=bundle.base)
    with pytest.raises(PublicationDenied, match="publication_secret_detected"):
        _publisher(fake, secret).service.publish(**secret.publish_args())
    assert fake.commit_posts == 0

    ci = _variant(
        bundle,
        {".gitlab-ci.yml": "job: {}\n", "models/fct_orders.sql": "select 2"},
        changed_paths=["models/fct_orders.sql", ".gitlab-ci.yml"],
    )
    fake_ci = FakeGitLab(base_archive=bundle.base)
    with pytest.raises(PublicationDenied, match=r"publication_path_forbidden:\.gitlab-ci\.yml"):
        _publisher(fake_ci, ci).service.publish(**ci.publish_args())
    assert fake_ci.commit_posts == 0

    custom_ci = _variant(
        bundle,
        {"models/fct_orders.sql": "select 1", "models/pipeline.yml": "job: {}\n"},
        changed_paths=["models/pipeline.yml"],
    )
    fake_custom_ci = FakeGitLab(base_archive=bundle.base)
    fake_custom_ci.ci_config_path = "models/pipeline.yml"
    with pytest.raises(PublicationDenied, match=r"publication_path_forbidden:models/pipeline\.yml"):
        _publisher(fake_custom_ci, custom_ci).service.publish(**custom_ci.publish_args())
    assert fake_custom_ci.commit_posts == 0

    for scopes in (["read_api"], ["read_api", "write_repository"]):
        readonly = FakeGitLab(base_archive=bundle.base, token_scopes=scopes)
        readonly_harness = _publisher(readonly, bundle)
        with pytest.raises(PublicationDenied) as insufficient:
            readonly_harness.service.publish(**bundle.publish_args())
        assert (
            insufficient.value.reason_code == CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT
        )
        assert set(readonly_harness.connectors.requested) == {"gitlab-1"}
        assert readonly.commit_posts == 0
        assert readonly.archive_downloads == 0

    missing_pat = FakeGitLab(base_archive=bundle.base)
    missing_pat.missing_token_info = True
    missing_pat.force_commit_forbidden = True
    missing_harness = _publisher(missing_pat, bundle)
    with pytest.raises(PublicationDenied) as missing:
        missing_harness.service.publish(**bundle.publish_args())
    assert missing.value.reason_code == CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT
    assert missing.value.receipt is not None
    assert missing.value.receipt.state == "reserved"
    assert missing.value.receipt.reason_code == CodingReasonCode.GITLAB_CONNECTOR_SCOPE_INSUFFICIENT
    assert set(missing_harness.connectors.requested) == {"gitlab-1"}
    assert missing_pat.commit_posts == 1


def test_publish_signature_does_not_accept_url_or_token() -> None:
    params = inspect.signature(GitLabPublicationService.publish).parameters
    assert {"url", "token", "api_token", "base_url"}.isdisjoint(params)


def test_ac21_retry_and_worker_restart_reuse_one_commit_and_mr() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    ledger = InMemoryLedger()
    first = _publisher(fake, bundle, ledger=ledger).service.publish(**bundle.publish_args())
    retried = _publisher(fake, bundle, ledger=ledger).service.publish(**bundle.publish_args())
    restarted = _publisher(fake, bundle, ledger=ledger).service.publish(**bundle.publish_args())
    assert first.id == retried.id == restarted.id
    assert restarted.state == "mr_recorded"
    assert fake.commit_posts == 1
    assert fake.mr_posts == 1
    _assert_fenced(fake.requests)


def test_ac21_parallel_publishers_converge_on_one_commit_and_mr() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    ledger = InMemoryLedger()
    workers = [_publisher(fake, bundle, ledger=ledger) for _ in range(4)]
    barrier = threading.Barrier(len(workers))

    def publish(harness: Harness) -> PublicationReceipt:
        barrier.wait()
        return harness.service.publish(**bundle.publish_args())

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        results = list(pool.map(publish, workers))
    assert {item.id for item in results} == {results[0].id}
    assert {item.state for item in results} == {"mr_recorded"}
    assert {item.commit_sha for item in results} == {fake.branches[BRANCH]["commit"]["id"]}
    assert len(fake.branches) == 1
    assert len(fake.merge_requests) == 1
    stored = _stored(ledger)
    assert stored is not None
    assert stored.state == "mr_recorded"
    _assert_fenced(fake.requests)


def test_cancel_before_commit_does_not_mutate_gitlab() -> None:
    bundle = _bundle()
    fake = FakeGitLab(base_archive=bundle.base)
    harness = _publisher(fake, bundle)
    harness.work_items.item = replace(bundle.work_item, cancelled=True)
    with pytest.raises(PublicationDenied, match="publication_cancelled"):
        harness.service.publish(**bundle.publish_args())
    assert fake.commit_posts == 0
    assert fake.mr_posts == 0
