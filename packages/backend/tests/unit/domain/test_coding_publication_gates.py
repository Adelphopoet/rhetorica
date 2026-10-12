from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from datetime import UTC, datetime

import pytest
from rhetorica_backend.contracts.coding import (
    AgentDelegationGrant,
    AgentPolicySnapshot,
    CandidateManifest,
    CodingRun,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)
from rhetorica_backend.domain.coding.publication import PublicationPreparer, PublicationWorkItem
from rhetorica_backend.domain.coding.publication_content_policy import (
    DbtPublicationDenyList,
    PublicationDenied,
    PublicationSecretScanner,
)
from rhetorica_backend.domain.coding.publication_diff import (
    diff_trees,
    tree_from_candidate_archive,
    tree_from_gitlab_archive,
)
from rhetorica_backend.domain.coding.publication_mr import draft_mr_description
from rhetorica_backend.infra.coding.archives import ArchivePolicy, UnsafeArchiveError

BASE_SHA = "b" * 40


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


def test_tree_from_gitlab_archive_strips_wrapper_and_skips_git() -> None:
    payload = _tar(
        {
            "models/fct_orders.sql": "select 1",
            ".git/HEAD": "ref: refs/heads/main",
        },
        prefix="analytics-dbt-bbbb/",
    )
    tree = tree_from_gitlab_archive(payload)
    assert tree == {"models/fct_orders.sql": b"select 1"}


def test_diff_detects_update_delete_and_rename() -> None:
    base = {
        "models/old.sql": b"select 1",
        "models/gone.sql": b"select 2",
        "models/keep.sql": b"select 3",
    }
    candidate = {
        "models/new.sql": b"select 1",
        "models/keep.sql": b"select 3",
        "models/added.sql": b"select 4",
    }
    result = diff_trees(
        base=base,
        candidate=candidate,
        changed_paths=["models/old.sql", "models/new.sql", "models/gone.sql", "models/added.sql"],
    )
    actions = {(item.action, item.file_path, item.previous_path) for item in result.changes}
    assert ("move", "models/new.sql", "models/old.sql") in actions
    assert ("delete", "models/gone.sql", None) in actions
    assert ("create", "models/added.sql", None) in actions
    assert result.unexpected_paths == ()


def test_diff_flags_undeclared_path_and_denylist_blocks_ci() -> None:
    result = diff_trees(
        base={"models/fct_orders.sql": b"select 1"},
        candidate={
            "models/fct_orders.sql": b"select 2",
            ".gitlab-ci.yml": "job: {}\n",
        },
        changed_paths=["models/fct_orders.sql"],
    )
    assert ".gitlab-ci.yml" in result.unexpected_paths
    assert DbtPublicationDenyList().reason_for(".gitlab-ci.yml") is not None
    assert DbtPublicationDenyList().reason_for("models/fct_orders.sql") is None


def test_candidate_archive_rejects_traversal() -> None:
    with pytest.raises(UnsafeArchiveError, match="archive_path_invalid"):
        tree_from_candidate_archive(_tar({"../secret.sql": "select 1"}))


def _scope() -> GitLabWriteScope:
    return GitLabWriteScope(
        connector_id="gitlab-1",
        project_id="analytics/dbt",
        target_branches=["main"],
        allowed_paths=["models"],
    )


def _settings() -> GovernedAgentSettings:
    return GovernedAgentSettings(
        revision=2,
        gitlab_write_scopes=[_scope()],
        allowed_subagent_ids=[],
        delegation_grants=[],
    )


def _root_settings() -> GovernedAgentSettings:
    return GovernedAgentSettings(
        revision=5,
        allowed_subagent_ids=["developer"],
        delegation_grants=[
            AgentDelegationGrant(subagent_id="developer", gitlab_write_scopes=[_scope()])
        ],
    )


def _run(candidate: CandidateManifest, report_id: str) -> CodingRun:
    now = datetime(2026, 9, 22, 8, tzinfo=UTC)
    return CodingRun(
        id="run-success",
        workspace_id="analytics",
        owner_user_id="user-1",
        source="web_chat",
        conversation_id="conversation-1",
        root_agent_id="manager",
        developer_agent_id="developer",
        qa_agent_id="qa",
        idempotency_key="request-1",
        input_digest="a" * 64,
        phase="publishing",
        status="running",
        repository={
            "gitlab_connector_id": "gitlab-1",
            "project_id": "analytics/dbt",
            "target_branch": "main",
            "base_sha": BASE_SHA,
        },
        environment_profile_id="dbt",
        environment_profile_revision=1,
        policy_revision=3,
        candidate=candidate,
        validation_report_id=report_id,
        created_at=now,
        deadline=datetime(2026, 9, 22, 8, 20, tzinfo=UTC),
    )


def _candidate(digest: str, paths: list[str], size: int) -> CandidateManifest:
    return CandidateManifest(
        artifact_id="candidate-1",
        workspace_id="analytics",
        owner_user_id="user-1",
        run_id="run-success",
        digest=digest,
        base_sha=BASE_SHA,
        changed_paths=paths,
        size_bytes=size,
        created_at=datetime(2026, 9, 22, 9, tzinfo=UTC),
    )


def _report(*, digest: str, checks: list[ValidationCheck]) -> ValidationReport:
    return ValidationReport(
        id="report-1",
        workspace_id="analytics",
        run_id="run-success",
        candidate_digest=digest,
        report_digest="d" * 64,
        checks=checks,
        qa_agent_id="qa",
        created_at=datetime(2026, 9, 22, 8, 10, tzinfo=UTC),
    )


def _principal() -> ExecutionPrincipal:
    return ExecutionPrincipal(
        actor_user_id="user-1",
        workspace_id="analytics",
        entry_source="web_chat",
        root_agent_id="manager",
        parent_agent_id="manager",
        executing_agent_id="developer",
        run_id="run-success",
        task_id="task-1",
    )


def _work_item(run: CodingRun, report: ValidationReport) -> PublicationWorkItem:
    executing = _settings()
    root = _root_settings()
    return PublicationWorkItem(
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


def _prepare(
    candidate: CandidateManifest,
    report: ValidationReport,
    payload: bytes,
    *,
    preparer: PublicationPreparer | None = None,
    persisted_report: ValidationReport | None = None,
    denied_paths: tuple[str, ...] = (),
):
    run = _run(candidate, "report-1")
    return (preparer or PublicationPreparer()).prepare(
        run_id=run.id,
        candidate=candidate,
        report=report,
        principal=_principal(),
        work_item=_work_item(run, persisted_report or report),
        candidate_payload=payload,
        base_tree={"models/fct_orders.sql": b"select 1"},
        denied_paths=denied_paths,
    )


def test_preparer_rejects_digest_mismatch_and_failed_mandatory_check() -> None:
    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(payload))
    passed = [ValidationCheck(name="dbt_compile", status=ValidationStatus.PASSED, mandatory=True)]
    failed = [ValidationCheck(name="dbt_compile", status=ValidationStatus.FAILED, mandatory=True)]
    with pytest.raises(PublicationDenied, match="publication_candidate_digest_mismatch"):
        _prepare(candidate, _report(digest="0" * 64, checks=passed), payload)
    with pytest.raises(PublicationDenied, match="publication_mandatory_failed"):
        _prepare(candidate, _report(digest=digest, checks=failed), payload)


def test_preparer_requires_the_report_persisted_for_the_run() -> None:
    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(payload))
    persisted = _report(
        digest=digest,
        checks=[ValidationCheck(name="dbt_compile", status="failed", mandatory=True)],
    )
    forged = _report(
        digest=digest,
        checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
    )
    with pytest.raises(PublicationDenied, match="publication_report_mismatch"):
        _prepare(candidate, forged, payload, persisted_report=persisted)
    with pytest.raises(PublicationDenied, match="publication_report_not_persisted"):
        _prepare(
            candidate,
            forged,
            payload,
            persisted_report=forged.model_copy(update={"id": "report-2"}),
        )


def test_preparer_allows_optional_materialization_not_run() -> None:
    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(payload))
    report = _report(
        digest=digest,
        checks=[
            ValidationCheck(name="dbt_compile", status="passed", mandatory=True),
            ValidationCheck(
                name="materialization",
                status="not_run",
                mandatory=False,
                reason="Read-only validation cannot materialize the changed model.",
            ),
        ],
    )
    prepared = _prepare(candidate, report, payload)
    assert prepared.branch_name == "feature/rhetorica/run-success"
    assert prepared.actions[0].action == "update"
    assert "not_run" in prepared.description_without_commit


def test_mr_description_keeps_report_text_inert() -> None:
    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(payload))
    report = _report(
        digest=digest,
        checks=[
            ValidationCheck(
                name="dbt_compile\n/label ~prod",
                status="passed",
                mandatory=True,
            ),
            ValidationCheck(
                name="materialization",
                status="not_run",
                mandatory=False,
                reason="skipped\n/ready\n/assign @all | `drop` " + "x" * 400,
            ),
        ],
    )
    description = draft_mr_description(
        run_id="run-success", candidate=candidate, report=report, commit_sha="c" * 40
    )
    lines = description.splitlines()
    assert not any(line.lstrip().startswith("/") for line in lines)
    assert "`dbt_compile /label ~prod`" in description
    assert "`skipped /ready /assign @all \\| 'drop' " in description
    for line in lines:
        head, mention, _ = line.partition("@all")
        if mention:
            assert head.count("`") % 2 == 1
    assert "x" * 400 not in description
    assert "…`" in description


def test_preparer_rejects_secret_and_oversize() -> None:
    secret_payload = _tar({"models/fct_orders.sql": "token = glpat-" + ("a" * 24)})
    digest = hashlib.sha256(secret_payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(secret_payload))
    report = _report(
        digest=digest,
        checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
    )
    with pytest.raises(PublicationDenied, match="publication_secret_detected"):
        _prepare(candidate, report, secret_payload)

    huge = _tar({"models/fct_orders.sql": "x" * 50})
    huge_digest = hashlib.sha256(huge).hexdigest()
    huge_candidate = _candidate(huge_digest, ["models/fct_orders.sql"], len(huge))
    with pytest.raises(PublicationDenied):
        _prepare(
            huge_candidate,
            _report(
                digest=huge_digest,
                checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
            ),
            huge,
            preparer=PublicationPreparer(archive_policy=ArchivePolicy(max_file_bytes=10)),
        )


def test_denylist_blocks_git_ci_profiles_exports_and_declared_size() -> None:
    deny = DbtPublicationDenyList()
    assert deny.reason_for(".git/config") is not None
    assert deny.reason_for("vendor/pkg/.git/config") is not None
    assert deny.reason_for(".gitlab-ci.yml") is not None
    assert deny.reason_for(".gitlab-ci.yaml") is not None
    assert deny.reason_for("ci/nightly-gitlab-ci.yaml") is not None
    assert deny.reason_for("credentials.yml") is not None
    assert deny.reason_for("models/credentials.yml") is not None
    assert deny.reason_for("credentials.yaml") is not None
    assert deny.reason_for("models/credentials.yaml") is not None
    assert deny.reason_for("models/target/run.json") is not None
    assert deny.reason_for("exports/dump.csv") is not None
    assert deny.reason_for("models/fct_orders.parquet") is not None
    assert deny.reason_for("models/fct_orders.sql") is None
    assert deny.reason_for("models/ci.yml") is None
    assert deny.reason_for("models/ci.yml", denied_paths=["/models/ci.yml"]) == (
        "publication_path_forbidden:models/ci.yml"
    )

    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    report = _report(
        digest=digest,
        checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
    )
    with pytest.raises(
        PublicationDenied, match=r"publication_path_forbidden:models/fct_orders\.sql"
    ):
        _prepare(
            _candidate(digest, ["models/fct_orders.sql"], len(payload)),
            report,
            payload,
            denied_paths=("models/fct_orders.sql",),
        )
    oversized = _candidate(
        digest, ["models/fct_orders.sql"], ArchivePolicy(max_total_bytes=8).max_total_bytes + 1
    )
    with pytest.raises(PublicationDenied, match="publication_candidate_oversize"):
        _prepare(
            oversized,
            report,
            payload,
            preparer=PublicationPreparer(archive_policy=ArchivePolicy(max_total_bytes=8)),
        )


def test_denylist_and_scanner_block_credentials_yaml_and_access_token() -> None:
    deny = DbtPublicationDenyList()
    assert deny.reason_for("models/credentials.yaml") == (
        "publication_path_forbidden:models/credentials.yaml"
    )
    scanner = PublicationSecretScanner()
    assert (
        scanner.reason_for(path="models/settings.yml", content=b"access_token: secret-value\n")
        == "publication_secret_detected:models/settings.yml"
    )

    yaml_payload = _tar({
        "models/fct_orders.sql": "select 2",
        "models/credentials.yaml": "user: analyst\n",
    })
    yaml_digest = hashlib.sha256(yaml_payload).hexdigest()
    with pytest.raises(
        PublicationDenied, match=r"publication_path_forbidden:models/credentials\.yaml"
    ):
        _prepare(
            _candidate(
                yaml_digest,
                ["models/fct_orders.sql", "models/credentials.yaml"],
                len(yaml_payload),
            ),
            _report(
                digest=yaml_digest,
                checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
            ),
            yaml_payload,
        )

    token_payload = _tar({
        "models/fct_orders.sql": "select 2",
        "models/settings.yml": "access_token: secret-value\n",
    })
    token_digest = hashlib.sha256(token_payload).hexdigest()
    with pytest.raises(PublicationDenied, match="publication_secret_detected"):
        _prepare(
            _candidate(
                token_digest,
                ["models/fct_orders.sql", "models/settings.yml"],
                len(token_payload),
            ),
            _report(
                digest=token_digest,
                checks=[ValidationCheck(name="dbt_compile", status="passed", mandatory=True)],
            ),
            token_payload,
        )


def test_preparer_rejects_blocked_mandatory_check() -> None:
    payload = _tar({"models/fct_orders.sql": "select 2"})
    digest = hashlib.sha256(payload).hexdigest()
    candidate = _candidate(digest, ["models/fct_orders.sql"], len(payload))
    report = _report(
        digest=digest,
        checks=[
            ValidationCheck(name="dbt_compile", status=ValidationStatus.BLOCKED, mandatory=True)
        ],
    )
    with pytest.raises(PublicationDenied, match="publication_mandatory_failed"):
        _prepare(candidate, report, payload)


def test_publication_requires_every_configured_reviewer():
    from rhetorica_backend.contracts.coding import CodingAgentTeamSnapshot
    from rhetorica_backend.domain.coding.publication_decision import (
        CodingPublicationDecisionService,
    )

    candidate = _candidate("a" * 64, ["models/a.sql"], 20)
    report = _report(
        digest=candidate.digest,
        checks=[ValidationCheck(name="independent_review:first", status="passed", mandatory=True)],
    )
    run = _run(candidate, report.id)
    run.team_snapshot = CodingAgentTeamSnapshot(
        team_id="team",
        revision_id="rev",
        revision=1,
        coordinator_agent_profile_id="coordinator",
        members=[
            {
                "id": "first",
                "label": "SQL",
                "agent_profile_id": "one",
                "capability": "review",
                "required_review": True,
            },
            {
                "id": "second",
                "label": "Performance",
                "agent_profile_id": "two",
                "capability": "review",
                "required_review": True,
            },
        ],
    )
    decision = CodingPublicationDecisionService().decide(run=run, report=report)
    assert not decision.allowed and decision.missing_reviews == ("independent_review:second",)
    with pytest.raises(PublicationDenied, match="Missing required reviews"):
        PublicationPreparer().authorize(
            run_id=run.id,
            candidate=candidate,
            report=report,
            principal=_principal(),
            work_item=_work_item(run, report),
        )
    report.checks.append(
        ValidationCheck(name="independent_review:second", status="passed", mandatory=True)
    )
    assert CodingPublicationDecisionService().decide(run=run, report=report).allowed
