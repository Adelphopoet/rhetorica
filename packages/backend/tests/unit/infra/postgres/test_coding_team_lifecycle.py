"""Coordinator-driven coding team lifecycle on the durable run repository."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    AgentTask,
    CandidateManifest,
    CodingAgentTeamSnapshot,
    CodingCoordinatorDecision,
    CodingReasonCode,
    CodingRun,
    CodingRunAdmission,
    CodingRunPhase,
    CodingRunStatus,
    EnvironmentProfile,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    QaVerdict,
    RepositoryBinding,
    ServerValidationEvidence,
)
from rhetorica_backend.domain.coding.activities import CodingActivities
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.lifecycle import (
    CodingLifecycleExecutor,
    CodingLifecycleService,
    CoordinatorExecution,
    DeveloperExecution,
    QaExecution,
)
from rhetorica_backend.domain.coding.publication_decision import CodingPublicationDecisionService
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from sqlalchemy import event

TEAM = CodingAgentTeamSnapshot.model_validate({
    "team_id": "team-1",
    "team_name": "dbt team",
    "revision_id": "revision-3",
    "revision": 3,
    "coordinator_agent_profile_id": "manager-1",
    "members": [
        {
            "id": "engineer",
            "agent_profile_id": "developer-1",
            "label": "Data engineer",
            "capability": "candidate",
        },
        {
            "id": "reviewer",
            "agent_profile_id": "qa-1",
            "label": "Reviewer",
            "capability": "review",
            "required_review": True,
        },
        {
            "id": "security",
            "agent_profile_id": "qa-2",
            "label": "Security reviewer",
            "capability": "review",
            "required_review": True,
        },
    ],
})


@pytest.fixture
def repository(tmp_path: Path) -> tuple[CodingRunRepository, str]:
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'team_runs.db'}")

    @event.listens_for(db.engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    db.create_schema()
    owner = UserAccountRepository(db).ensure_bootstrap_user("default")
    WorkspaceRepository(db).ensure_home_membership(user_id=owner.id)
    return CodingRunRepository(db), owner.id


def _admit(repository: CodingRunRepository, owner_id: str, *, max_iterations: int = 20):
    now = datetime.now(tz=UTC)
    run_id = str(uuid4())
    settings = GovernedAgentSettings(
        revision=1, max_iterations=max_iterations, max_runtime_seconds=600
    )
    run = CodingRun(
        id=run_id,
        workspace_id="default",
        owner_user_id=owner_id,
        source="web_scenario",
        scenario_id="scenario-1",
        root_agent_id="manager-1",
        developer_agent_id="developer-1",
        qa_agent_id="qa-1",
        team_snapshot=TEAM,
        idempotency_key=run_id,
        input_digest="a" * 64,
        phase=CodingRunPhase.PREPARING,
        status=CodingRunStatus.QUEUED,
        repository=RepositoryBinding(
            gitlab_connector_id="gitlab-1",
            project_id="group/project",
            target_branch="main",
            base_sha="b" * 40,
        ),
        environment_profile_id="coding-default",
        environment_profile_revision=1,
        policy_revision=1,
        created_at=now,
        deadline=now + timedelta(minutes=20),
    )
    admission = CodingRunAdmission(
        run=run,
        principal=ExecutionPrincipal(
            actor_user_id=owner_id,
            workspace_id="default",
            entry_source="web_scenario",
            root_agent_id="manager-1",
            executing_agent_id="developer-1",
            run_id=run_id,
        ),
        policy_snapshot=AgentPolicySnapshot(
            workspace_id="default",
            root_agent_id="manager-1",
            executing_agent_id="developer-1",
            root_settings=settings,
            executing_settings=settings,
            resource_connector_id="gitlab-1",
            run_scope=GitLabWriteScope(
                connector_id="gitlab-1",
                project_id="group/project",
                target_branches=["main"],
                allowed_paths=["models"],
            ),
        ),
        environment_profile=EnvironmentProfile(
            id="coding-default",
            revision=1,
            image_digest="registry.example/coding@sha256:" + "c" * 64,
            runtime_class_name="gvisor",
            max_runtime_seconds=1200,
            cpu_limit="1",
            memory_limit="1Gi",
            disk_limit_bytes=1024 * 1024 * 1024,
            pid_limit=256,
            created_at=now,
        ),
    )
    coordinator_id = str(uuid5(NAMESPACE_URL, f"coding/{run_id}/coordinator"))
    tasks = [
        AgentTask(
            id=coordinator_id,
            workspace_id="default",
            owner_user_id=owner_id,
            run_id=run_id,
            agent_profile_id="manager-1",
            role="coordinator",
            status=CodingRunStatus.QUEUED,
            max_iterations=max_iterations,
            max_runtime_seconds=600,
            instruction_artifact_id="artifact://instruction",
        ),
        *[
            AgentTask(
                id=str(uuid5(NAMESPACE_URL, f"coding/{run_id}/member/{member.id}")),
                workspace_id="default",
                owner_user_id=owner_id,
                run_id=run_id,
                parent_task_id=coordinator_id,
                agent_profile_id=member.agent_profile_id,
                role=member.storage_role,
                team_member_id=member.id,
                team_member_label=member.label,
                status=CodingRunStatus.QUEUED,
                max_iterations=max_iterations,
                max_runtime_seconds=600,
                instruction_artifact_id="artifact://instruction",
            )
            for member in TEAM.members
        ],
    ]
    return repository.admit(admission, tasks=tasks).run


class TeamExecutor(CodingLifecycleExecutor):
    """Plays a scripted coordinator; members return deterministic outcomes."""

    def __init__(self, script, verdicts=None) -> None:
        self.script = script
        self.verdicts = dict(verdicts or {})
        self.summaries: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.results: list[dict] = []
        self.candidates = 0

    async def prepare(self, *, admission):
        return None

    async def develop(self, *, admission, task, operation_id, deadline, assignment=""):
        self.calls.append((task.team_member_id, assignment))
        self.candidates += 1
        digest = f"{self.candidates:x}" * 64
        return DeveloperExecution(
            candidate=CandidateManifest(
                artifact_id=f"artifact://candidate-{self.candidates}",
                workspace_id=admission.run.workspace_id,
                owner_user_id=admission.run.owner_user_id,
                run_id=admission.run.id,
                digest=digest[:64],
                base_sha=admission.run.repository.base_sha,
                changed_paths=["models/orders.sql"],
                size_bytes=10,
                created_at=datetime.now(tz=UTC),
            ),
            checkpoint_ref=f"checkpoint://{self.candidates}",
            summary=f"candidate {self.candidates}",
        )

    async def qa(self, *, admission, task, candidate, operation_id, deadline, assignment=""):
        self.calls.append((task.team_member_id, assignment))
        status = self.verdicts.get((task.team_member_id, candidate.digest), "passed")
        now = datetime.now(tz=UTC)
        return QaExecution(
            verdict=QaVerdict(
                id=str(uuid4()),
                qa_agent_id=task.agent_profile_id,
                task_id=task.id,
                candidate_digest=candidate.digest,
                status=status,
                reason=f"{task.team_member_label} {status}",
                created_at=now,
            ),
            evidence=(
                ServerValidationEvidence(
                    id=str(uuid4()),
                    workspace_id=admission.run.workspace_id,
                    owner_user_id=admission.run.owner_user_id,
                    run_id=admission.run.id,
                    task_id=task.id,
                    candidate_digest=candidate.digest,
                    name="candidate_integrity",
                    source="system",
                    status="passed",
                    mandatory=True,
                    duration_ms=0,
                    created_at=now,
                ),
            ),
            checkpoint_ref=candidate.artifact_id,
            summary=self.summaries.get(task.team_member_id, ""),
        )

    async def coordinate(self, *, admission, task, operation_id, deadline, invoke_member):
        for index, (member_id, assignment) in enumerate(self.script[:-1]):
            self.results.append(await invoke_member(member_id, f"call-{index}", assignment))
        decision, summary = self.script[-1]
        return CoordinatorExecution(
            decision=CodingCoordinatorDecision(decision=decision, summary=summary)
        )

    async def publish(self, *, admission, candidate, report):
        raise AssertionError("publication is checked separately")

    async def cancel(self, *, admission):
        return ("artifact://partial",)


def _run(repository, run):
    return repository.get_admission_for_worker(run_id=run.id).run


def test_coordinator_returns_review_findings_to_candidate_member(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    first_digest = "1" * 64
    executor = TeamExecutor(
        [
            ("reviewer", "Review before any change"),
            ("engineer", "Add net_revenue"),
            ("reviewer", "Review the candidate"),
            ("engineer", "Fix: refunds are not subtracted"),
            ("reviewer", "Review the fix"),
            ("security", "Check for secrets"),
            ("publish", "Candidate reviewed by both reviewers."),
        ],
        verdicts={("reviewer", first_digest): "failed"},
    )
    lifecycle = CodingLifecycleService(runs=runs, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))

    decision = asyncio.run(lifecycle.coordinate(run_id=run.id, turn=0))
    _, publish = asyncio.run(lifecycle.finish_turn(run_id=run.id, decision=decision))

    assert executor.results[0]["status"] == "blocked"
    assert executor.results[2]["status"] == "failed"
    assert executor.calls[2] == ("engineer", "Fix: refunds are not subtracted")
    assert [result["status"] for result in executor.results[3:]] == [
        "candidate_recorded",
        "passed",
        "passed",
    ]
    assert publish is True
    current = _run(runs, run)
    report = runs.validation_report(
        report_id=current.validation_report_id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )
    assert report.candidate_digest == current.candidate.digest == "2" * 64
    assert {check.name for check in report.checks} >= {
        "independent_review:reviewer",
        "independent_review:security",
    }
    assert CodingPublicationDecisionService().decide(run=current, report=report).allowed


def test_failed_second_review_blocks_publication_of_same_candidate(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    executor = TeamExecutor(
        [
            ("engineer", "Implement"),
            ("reviewer", "Review"),
            ("security", "Security review"),
            ("reviewer", "Review again"),
            ("publish", "Ship it"),
        ],
        verdicts={("security", "1" * 64): "failed"},
    )
    lifecycle = CodingLifecycleService(runs=runs, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))
    decision = asyncio.run(lifecycle.coordinate(run_id=run.id, turn=0))

    current = _run(runs, run)
    report = runs.validation_report(
        report_id=current.validation_report_id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )

    # A repeated review does not run the model again; the recorded verdict is binding.
    assert executor.results[3]["status"] == "passed"
    assert executor.results[3]["repeat"] is True
    assert [member for member, _ in executor.calls] == ["engineer", "reviewer", "security"]
    assert decision.decision == "publish"
    assert not CodingPublicationDecisionService().decide(run=current, report=report).allowed


def test_skipped_required_review_is_persisted_as_not_run_and_blocks_publication(repository):
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    lifecycle = CodingLifecycleService(
        runs=runs,
        executor=TeamExecutor([
            ("engineer", "Implement"),
            ("reviewer", "Review"),
            ("publish", "Ship it"),
        ]),
    )
    asyncio.run(lifecycle.prepare(run_id=run.id))
    decision = asyncio.run(lifecycle.coordinate(run_id=run.id, turn=0))
    _, publish = asyncio.run(lifecycle.finish_turn(run_id=run.id, decision=decision))
    assert publish
    stopped = asyncio.run(lifecycle.publish(run_id=run.id))
    assert stopped.status == CodingRunStatus.FAILED
    assert stopped.reason_code == CodingReasonCode.POLICY_DENIED
    report = runs.validation_report(
        report_id=stopped.validation_report_id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=owner_id,
    )
    checks = {check.name: check for check in report.checks}
    assert checks["independent_review:reviewer"].status == "passed"
    pending = checks["independent_review:security"]
    assert pending.status == "not_run" and pending.mandatory
    assert pending.receipt_id is None
    assert pending.reason == "Не выполнена обязательная проверка: Security reviewer"


def test_coordinator_stop_is_an_explicit_terminal_result(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    lifecycle = CodingLifecycleService(
        runs=runs,
        executor=TeamExecutor([("stop", "The model needs a business definition of refunds.")]),
    )
    asyncio.run(lifecycle.prepare(run_id=run.id))

    payload = asyncio.run(CodingActivities(lifecycle).coordinate({"run_id": run.id, "turn": 0}))
    stopped = _run(runs, run)

    assert payload["terminal"] is True and payload["publish"] is False
    assert stopped.status == CodingRunStatus.FAILED
    assert stopped.reason_code == CodingReasonCode.COORDINATOR_STOPPED
    assert stopped.partial_result.summary == "The model needs a business definition of refunds."
    assert stopped.partial_result.artifact_ids == ["artifact://partial"]


def test_publish_request_without_reviewed_candidate_is_refused(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    lifecycle = CodingLifecycleService(
        runs=runs, executor=TeamExecutor([("engineer", "Implement"), ("publish", "Done")])
    )
    asyncio.run(lifecycle.prepare(run_id=run.id))

    payload = asyncio.run(CodingActivities(lifecycle).coordinate({"run_id": run.id, "turn": 0}))

    assert payload == {
        "terminal": True,
        "publish": False,
        "decision": "publish",
        "status": CodingRunStatus.FAILED,
    }
    assert _run(runs, run).reason_code == CodingReasonCode.POLICY_DENIED


def test_revoked_member_grant_during_run_stops_without_publication(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)

    class RevokingExecutor(TeamExecutor):
        async def coordinate(self, *, admission, task, operation_id, deadline, invoke_member):
            raise CodingRunError("coding_delegation_not_allowed", status_code=403)

    lifecycle = CodingLifecycleService(runs=runs, executor=RevokingExecutor([("stop", "-")]))
    asyncio.run(lifecycle.prepare(run_id=run.id))

    payload = asyncio.run(CodingActivities(lifecycle).coordinate({"run_id": run.id, "turn": 0}))
    stopped = _run(runs, run)

    assert payload["terminal"] is True
    assert stopped.status == CodingRunStatus.FAILED
    assert stopped.reason_code == CodingReasonCode.POLICY_DENIED


def test_member_budget_exhaustion_is_a_durable_limit_result(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id, max_iterations=1)
    executor = TeamExecutor([("engineer", "One"), ("engineer", "Two"), ("stop", "-")])
    lifecycle = CodingLifecycleService(runs=runs, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))

    payload = asyncio.run(CodingActivities(lifecycle).coordinate({"run_id": run.id, "turn": 0}))

    assert payload["terminal"] is True
    assert [member for member, _ in executor.calls] == ["engineer"]
    assert _run(runs, run).reason_code == CodingReasonCode.ITERATION_LIMIT_REACHED


def test_unknown_member_is_not_routed_to_another_task(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    lifecycle = CodingLifecycleService(runs=runs, executor=TeamExecutor([("stop", "-")]))

    with pytest.raises(CodingRunError, match="coding_team_member_unknown"):
        asyncio.run(
            lifecycle.invoke_member(
                run_id=run.id, member_id="outsider", call_id="call-1", assignment="Do it"
            )
        )


def test_member_reservation_key_fits_its_column_for_long_provider_call_ids(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    invocations: list[str] = []
    reserve = runs.reserve_iteration

    def recording_reserve(**kwargs):
        invocations.append(kwargs["invocation_id"])
        return reserve(**kwargs)

    runs.reserve_iteration = recording_reserve
    lifecycle = CodingLifecycleService(runs=runs, executor=TeamExecutor([("stop", "-")]))
    asyncio.run(lifecycle.prepare(run_id=run.id))

    asyncio.run(
        lifecycle.invoke_member(
            run_id=run.id, member_id="engineer", call_id="call_" + "x" * 200, assignment="Do it"
        )
    )

    assert invocations and all(len(value) <= 128 for value in invocations)


def test_member_executor_receives_the_complete_assignment(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    assignment = "Создай модель fct_omd__infcategory_database_fqn_snapshot. " * 120
    executor = TeamExecutor([("engineer", assignment), ("stop", "-")])
    lifecycle = CodingLifecycleService(runs=runs, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))

    asyncio.run(lifecycle.coordinate(run_id=run.id, turn=0))

    assert executor.calls == [("engineer", assignment)]


def test_coordinator_receives_the_full_review_report(repository) -> None:
    runs, owner_id = repository
    run = _admit(runs, owner_id)
    findings = "Дефект документации: grain описан неверно. " * 60
    executor = TeamExecutor(
        [("engineer", "Implement"), ("reviewer", "Review"), ("stop", "-")],
        verdicts={("reviewer", "1" * 64): "failed"},
    )
    executor.summaries["reviewer"] = findings
    lifecycle = CodingLifecycleService(runs=runs, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))

    asyncio.run(lifecycle.coordinate(run_id=run.id, turn=0))

    assert executor.results[1]["status"] == "failed"
    assert executor.results[1]["reason"] == findings
