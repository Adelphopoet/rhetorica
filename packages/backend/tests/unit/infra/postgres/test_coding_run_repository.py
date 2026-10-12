from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from rhetorica_backend.contracts.agents import UpsertAgentProfileRequest
from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    AgentTask,
    CandidateManifest,
    CodingPartialResult,
    CodingReasonCode,
    CodingRun,
    CodingRunAdmission,
    CodingRunPhase,
    CodingRunStatus,
    CodingTeamMemberCapability,
    CreateCodingDelegationRequest,
    EnvironmentProfile,
    ExecutionPrincipal,
    GitLabWriteScope,
    GovernedAgentSettings,
    PublicationReceipt,
    QaVerdict,
    RepositoryBinding,
    ServerValidationEvidence,
    SkillReadAddress,
    ValidationReport,
)
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillVersionBinding
from rhetorica_backend.domain.coding.activities import CodingActivities
from rhetorica_backend.domain.coding.agents import (
    CodingSpecialistAgentFactory,
    CodingToolAuthorizationService,
)
from rhetorica_backend.domain.coding.deadlines import CodingDeadlineCoordinator
from rhetorica_backend.domain.coding.delegation import CodingDelegationService
from rhetorica_backend.domain.coding.errors import (
    CodingBudgetExhausted,
    CodingDelegationDenied,
    CodingRunConflict,
    CodingRunNotFound,
    CodingTaskConflict,
    CodingToolAccessDenied,
)
from rhetorica_backend.domain.coding.lifecycle import (
    CodingLifecycleExecutor,
    CodingLifecycleService,
    DeveloperExecution,
    QaExecution,
)
from rhetorica_backend.domain.coding.progress import CodingRunProgressReader
from rhetorica_backend.domain.coding.publication_content_policy import PublicationDenied
from rhetorica_backend.domain.coding.skill_reader import CodingWorkspaceSkillReader
from rhetorica_backend.domain.coding.temporal_workflow import CodingWorkflow
from rhetorica_backend.domain.coding.validation import ValidationReportAssembler
from rhetorica_backend.domain.coding.worker import CodingWorkerSettings, dispatch_coding_outbox
from rhetorica_backend.infra.postgres.agent_policy_repository import AgentPolicyRepository
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_run_models import CodingBudgetLedgerOrm
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository
from sqlalchemy import event, select


@pytest.fixture
def setup(tmp_path: Path):
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'coding_runs.db'}")

    @event.listens_for(db.engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    db.create_schema()
    users = UserAccountRepository(db)
    owner = users.ensure_bootstrap_user("default")
    WorkspaceRepository(db).ensure_home_membership(user_id=owner.id)
    other = users.create_local_user(
        workspace_id="default",
        login="other",
        password_hash="test-only",
        role="analyst",
    )
    WorkspaceRepository(db).ensure_home_membership(user_id=other.id)
    return db, owner, other


def _admission(
    *,
    run_id: str,
    owner_user_id: str,
    input_digest: str = "a" * 64,
    max_iterations: int = 3,
    max_runtime_seconds: int = 300,
):
    now = datetime.now(tz=UTC)
    run = CodingRun(
        id=run_id,
        workspace_id="default",
        owner_user_id=owner_user_id,
        source="web_scenario",
        scenario_id="scenario-1",
        root_agent_id="manager-1",
        developer_agent_id="developer-1",
        qa_agent_id="qa-1",
        idempotency_key="request-1",
        input_digest=input_digest,
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
    scope = GitLabWriteScope(
        connector_id="gitlab-1",
        project_id="group/project",
        target_branches=["main"],
        allowed_paths=["models"],
    )
    principal = ExecutionPrincipal(
        actor_user_id=owner_user_id,
        workspace_id="default",
        entry_source="web_scenario",
        root_agent_id="manager-1",
        executing_agent_id="developer-1",
        run_id=run.id,
    )
    return CodingRunAdmission(
        run=run,
        principal=principal,
        policy_snapshot=AgentPolicySnapshot(
            workspace_id="default",
            root_agent_id="manager-1",
            executing_agent_id="developer-1",
            root_settings=GovernedAgentSettings(
                revision=1,
                max_iterations=max_iterations,
                max_runtime_seconds=max_runtime_seconds,
            ),
            executing_settings=GovernedAgentSettings(
                revision=1,
                max_iterations=max_iterations,
                max_runtime_seconds=max_runtime_seconds,
            ),
            resource_connector_id="gitlab-1",
            run_scope=scope,
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


def _task(
    *,
    run_id: str,
    owner_user_id: str,
    task_id: str = "task-1",
    agent_profile_id: str = "developer-1",
    role: str = "developer",
    max_iterations: int = 3,
    max_runtime_seconds: int = 300,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        workspace_id="default",
        owner_user_id=owner_user_id,
        run_id=run_id,
        agent_profile_id=agent_profile_id,
        role=role,  # type: ignore[arg-type]
        status=CodingRunStatus.QUEUED,
        max_iterations=max_iterations,
        max_runtime_seconds=max_runtime_seconds,
        instruction_artifact_id="artifact://instruction-1",
        skill_version_digests=["digest-1"],
        skill_version_bindings=[
            WorkspaceSkillVersionBinding(skill_id="skill-1", version_digest="digest-1")
        ],
        skill_read_addresses=[
            SkillReadAddress(
                skill_id="skill-1",
                version_digest="digest-1",
                path="SKILL.md",
                offset=0,
                max_chars=1000,
            )
        ],
    )


def _candidate(*, run: CodingRun, digest: str = "d" * 64) -> CandidateManifest:
    return CandidateManifest(
        artifact_id="artifact://candidate-1",
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
        run_id=run.id,
        digest=digest,
        base_sha=run.repository.base_sha,
        changed_paths=["models/customer.sql"],
        size_bytes=128,
        created_at=datetime.now(tz=UTC),
    )


def _publication_ready(
    repository: CodingRunRepository,
    *,
    run: CodingRun,
    owner_user_id: str,
) -> tuple[CandidateManifest, ValidationReport]:
    developer = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner_user_id, task_id="developer-task")
    )
    qa = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner_user_id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )
    candidate = _candidate(run=run)
    repository.record_candidate(candidate)
    repository.record_validation_evidence(
        ServerValidationEvidence(
            id="receipt-compile",
            workspace_id=run.workspace_id,
            owner_user_id=owner_user_id,
            run_id=run.id,
            task_id=developer.id,
            candidate_digest=candidate.digest,
            name="dbt_compile",
            source="sandbox",
            status="passed",
            mandatory=True,
            exit_code=0,
            duration_ms=120,
            created_at=datetime.now(tz=UTC),
        )
    )
    report = ValidationReportAssembler(repository).assemble(
        candidate=candidate,
        qa_verdict=QaVerdict(
            id="qa-verdict-1",
            qa_agent_id=qa.agent_profile_id,
            task_id=qa.id,
            candidate_digest=candidate.digest,
            status="passed",
            created_at=datetime.now(tz=UTC),
        ),
    )
    return candidate, repository.save_validation_report(report=report, owner_user_id=owner_user_id)


def _publication_receipt(
    *, run: CodingRun, candidate: CandidateManifest, report: ValidationReport
) -> PublicationReceipt:
    now = datetime.now(tz=UTC)
    return PublicationReceipt(
        id="publication-receipt-1",
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
        run_id=run.id,
        candidate_digest=candidate.digest,
        validation_report_digest=report.report_digest,
        state="mr_recorded",
        branch_name="coding/test",
        merge_request_url="https://gitlab.example/mr/1",
        created_at=now,
        updated_at=now,
    )


def test_admission_is_atomic_idempotent_and_owner_scoped(setup) -> None:
    db, owner, other = setup
    repository = CodingRunRepository(db)
    first = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id))
    repeated = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id))

    assert first.created is True
    assert repeated.created is False
    assert repeated.run.id == first.run.id
    assert first.workflow_id == f"coding/{first.run.id}"
    assert [
        event.type
        for event in repository.events(
            run_id=first.run.id,
            workspace_id="default",
            owner_user_id=owner.id,
        )
    ] == ["run.queued"]
    assert len(repository.pending_outbox()) == 1
    assert repository.get_admission(
        run_id=first.run.id,
        workspace_id="default",
        owner_user_id=owner.id,
    ).environment_profile.image_digest.endswith("c" * 64)
    with pytest.raises(CodingRunNotFound):
        repository.get(
            run_id=first.run.id,
            workspace_id="default",
            owner_user_id=other.id,
        )


def test_idempotency_key_rejects_changed_input(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id))

    with pytest.raises(CodingRunConflict, match="coding_idempotency_key_reused"):
        repository.admit(
            _admission(
                run_id=str(uuid4()),
                owner_user_id=owner.id,
                input_digest="d" * 64,
            )
        )


def test_parallel_admission_returns_one_durable_run(setup) -> None:
    db, owner, _ = setup

    def admit(_: int):
        return CodingRunRepository(db).admit(
            _admission(run_id=str(uuid4()), owner_user_id=owner.id)
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(admit, range(8)))

    assert {result.run.id for result in results} == {results[0].run.id}
    assert sum(result.created for result in results) == 1
    assert len(CodingRunRepository(db).pending_outbox()) == 1


def test_task_preserves_skill_references_and_is_version_fenced(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    created = repository.create_task(_task(run_id=run.id, owner_user_id=owner.id))
    loaded = repository.get_task(
        task_id=created.id,
        workspace_id="default",
        owner_user_id=owner.id,
    )

    assert loaded.skill_version_bindings == created.skill_version_bindings
    assert loaded.skill_read_addresses == created.skill_read_addresses
    updated = repository.checkpoint_task(
        loaded.model_copy(update={"status": CodingRunStatus.RUNNING, "iterations_used": 1}),
        expected_version=loaded.version,
    )
    assert updated.version == 2
    assert updated.iterations_used == 1
    with pytest.raises(CodingTaskConflict, match="coding_task_stale_version"):
        repository.checkpoint_task(loaded, expected_version=loaded.version)
    assert [
        event.sequence
        for event in repository.events(
            run_id=run.id,
            workspace_id="default",
            owner_user_id=owner.id,
        )
    ] == [1, 2, 3]


def test_parallel_task_checkpoints_keep_event_cursor_monotonic(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    first = repository.create_task(_task(run_id=run.id, owner_user_id=owner.id, task_id="task-1"))
    second = repository.create_task(_task(run_id=run.id, owner_user_id=owner.id, task_id="task-2"))

    def checkpoint(task: AgentTask) -> AgentTask:
        return CodingRunRepository(db).checkpoint_task(
            task.model_copy(update={"status": CodingRunStatus.RUNNING, "iterations_used": 1}),
            expected_version=task.version,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        updated = list(executor.map(checkpoint, (first, second)))

    assert {task.version for task in updated} == {2}
    events = repository.events(
        run_id=run.id,
        workspace_id="default",
        owner_user_id=owner.id,
    )
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.type for event in events].count("task.checkpointed") == 2


def test_budget_reservations_are_retry_safe_and_concurrency_safe(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    task = repository.create_task(_task(run_id=run.id, owner_user_id=owner.id))

    first = repository.reserve_iteration(
        run_id=run.id,
        task_id=task.id,
        executing_agent_profile_id=task.agent_profile_id,
        invocation_id="developer-attempt-1",
        workspace_id="default",
        owner_user_id=owner.id,
    )
    retry = repository.reserve_iteration(
        run_id=run.id,
        task_id=task.id,
        executing_agent_profile_id=task.agent_profile_id,
        invocation_id="developer-attempt-1",
        workspace_id="default",
        owner_user_id=owner.id,
    )
    assert retry == first

    def reserve(index: int) -> str:
        try:
            reservation = CodingRunRepository(db).reserve_iteration(
                run_id=run.id,
                task_id=task.id,
                executing_agent_profile_id=task.agent_profile_id,
                invocation_id=f"developer-attempt-{index}",
                workspace_id="default",
                owner_user_id=owner.id,
            )
        except CodingBudgetExhausted:
            return "exhausted"
        return str(reservation.iteration)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(reserve, range(2, 7)))

    assert sorted(result for result in results if result != "exhausted") == ["2", "3"]
    assert results.count("exhausted") == 3
    [ledger] = repository.budget_ledgers(
        run_id=run.id,
        workspace_id="default",
        owner_user_id=owner.id,
    )
    assert ledger.reserved_iterations == 3
    assert ledger.first_attempt_at is not None
    assert retry.deadline == first.deadline


def test_developer_and_qa_share_the_root_agent_budget_for_one_run(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    developer = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id, task_id="developer-task")
    )
    qa = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )

    for index in range(2):
        reservation = repository.reserve_iteration(
            run_id=run.id,
            task_id=developer.id,
            executing_agent_profile_id=developer.agent_profile_id,
            invocation_id=f"developer-{index}",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )
        assert reservation.budget_owner_agent_id == "manager-1"
        assert reservation.iteration == index + 1
    qa_reservation = repository.reserve_iteration(
        run_id=run.id,
        task_id=qa.id,
        executing_agent_profile_id=qa.agent_profile_id,
        invocation_id="qa-0",
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )
    assert qa_reservation.iteration == 3
    with pytest.raises(CodingBudgetExhausted):
        repository.reserve_iteration(
            run_id=run.id,
            task_id=developer.id,
            executing_agent_profile_id=developer.agent_profile_id,
            invocation_id="developer-2",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )

    [ledger] = repository.budget_ledgers(
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )
    assert (ledger.budget_owner_agent_id, ledger.reserved_iterations, ledger.max_iterations) == (
        "manager-1",
        3,
        3,
    )


def test_task_local_iteration_limit_cannot_be_bypassed_through_shared_ledger(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(
        _admission(run_id=str(uuid4()), owner_user_id=owner.id, max_iterations=100)
    ).run
    specialist = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            max_iterations=1,
        )
    )

    reservation = repository.reserve_iteration(
        run_id=run.id,
        task_id=specialist.id,
        executing_agent_profile_id=specialist.agent_profile_id,
        invocation_id="specialist-first",
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )

    with pytest.raises(CodingBudgetExhausted, match="coding_iteration_limit_reached"):
        repository.reserve_iteration(
            run_id=run.id,
            task_id=specialist.id,
            executing_agent_profile_id=specialist.agent_profile_id,
            invocation_id="specialist-second",
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )

    assert reservation.iteration == 1
    assert (
        repository.get_task(
            task_id=specialist.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        ).iterations_used
        == 1
    )


def test_budget_reservation_stops_before_an_expired_task_can_call_a_model(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    expired = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id).model_copy(
            update={"deadline": datetime.now(tz=UTC) - timedelta(seconds=1)}
        )
    )

    with pytest.raises(CodingBudgetExhausted, match="coding_runtime_limit_reached"):
        repository.reserve_iteration(
            run_id=run.id,
            task_id=expired.id,
            executing_agent_profile_id=expired.agent_profile_id,
            invocation_id="expired-attempt",
            workspace_id="default",
            owner_user_id=owner.id,
        )
    assert (
        repository.budget_ledgers(
            run_id=run.id,
            workspace_id="default",
            owner_user_id=owner.id,
        )
        == []
    )


def test_deadline_clamps_operations_and_persists_partial_result_after_cancellation(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    admitted = _admission(run_id=str(uuid4()), owner_user_id=owner.id)
    run = repository.admit(admitted).run
    now = datetime.now(tz=UTC)
    task = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id).model_copy(
            update={"deadline": now + timedelta(seconds=7)}
        )
    )
    coordinator = CodingDeadlineCoordinator(repository)

    operation = coordinator.operation_deadline(
        run=run,
        task=task,
        environment=admitted.environment_profile,
        now=now,
    )
    stopped_handles: list[str] = []

    def query_already_stopped() -> None:
        raise RuntimeError("query already stopped")

    stopped = coordinator.cancel_or_expire(
        run=run,
        partial_result=CodingPartialResult(
            artifact_ids=["artifact://partial"],
            summary="Worker stopped before QA",
            stopped_reason=CodingReasonCode.RUNTIME_LIMIT_REACHED,
        ),
        reason_code=CodingReasonCode.RUNTIME_LIMIT_REACHED,
        cancelled=False,
        cancel_handles=(
            lambda: stopped_handles.append("sandbox"),
            query_already_stopped,
        ),
    )

    assert operation.timeout_seconds == 7
    assert operation.timeout_ms == 7_000
    assert stopped_handles == ["sandbox"]
    assert stopped.status == CodingRunStatus.FAILED
    assert stopped.partial_result is not None
    assert stopped.partial_result.artifact_ids == ["artifact://partial"]


def test_validation_report_uses_server_receipts_and_exact_qa_candidate(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    developer = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id, task_id="developer-task")
    )
    qa = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )
    candidate = _candidate(run=run)
    repository.record_candidate(candidate)
    receipt = repository.record_validation_evidence(
        ServerValidationEvidence(
            id="receipt-compile",
            workspace_id="default",
            owner_user_id=owner.id,
            run_id=run.id,
            task_id=developer.id,
            candidate_digest=candidate.digest,
            name="dbt_compile",
            source="sandbox",
            status="passed",
            mandatory=True,
            exit_code=0,
            duration_ms=120,
            artifact_id="artifact://checks/compile.log",
            created_at=datetime.now(tz=UTC),
        )
    )
    verdict = QaVerdict(
        id="qa-verdict-1",
        qa_agent_id=qa.agent_profile_id,
        task_id=qa.id,
        candidate_digest=candidate.digest,
        status="passed",
        created_at=datetime.now(tz=UTC),
    )

    report = ValidationReportAssembler(repository).assemble(
        candidate=candidate,
        qa_verdict=verdict,
    )
    persisted = repository.save_validation_report(report=report, owner_user_id=owner.id)

    assert persisted == report
    assert (
        repository.get(
            run_id=run.id,
            workspace_id="default",
            owner_user_id=owner.id,
        ).validation_report_id
        == report.id
    )
    assert (
        repository.validation_report(
            report_id=report.id,
            run_id=run.id,
            workspace_id="default",
            owner_user_id=owner.id,
        )
        == report
    )
    check = next(item for item in report.checks if item.receipt_id == receipt.id)
    assert check.exit_code == 0
    assert check.artifact_id == "artifact://checks/compile.log"
    assert "stdout" not in check.model_dump()

    with pytest.raises(CodingRunConflict, match="coding_validation_qa_candidate_mismatch"):
        ValidationReportAssembler(repository).assemble(
            candidate=candidate,
            qa_verdict=verdict.model_copy(update={"candidate_digest": "e" * 64}),
        )


def test_late_publication_receipt_keeps_cancelled_run_and_partial_result(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    candidate, report = _publication_ready(repository, run=run, owner_user_id=owner.id)
    partial_result = CodingPartialResult(
        artifact_ids=["artifact://partial"],
        summary="Owner cancelled while publisher was finishing.",
        stopped_reason=CodingReasonCode.CANCELLED_BY_OWNER,
    )
    repository.stop_with_partial_result(
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
        reason_code=CodingReasonCode.CANCELLED_BY_OWNER.value,
        partial_result=partial_result,
        cancelled=True,
    )

    persisted = repository.record_publication_receipt(
        _publication_receipt(run=run, candidate=candidate, report=report)
    )

    assert persisted.status == CodingRunStatus.CANCELLED
    assert persisted.reason_code == CodingReasonCode.CANCELLED_BY_OWNER
    assert persisted.partial_result == partial_result
    assert persisted.publication_receipt_id == "publication-receipt-1"
    assert (
        repository.events(
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )[-1].type
        == "publication.receipt_recorded_after_terminal"
    )


def test_publication_receipt_keeps_cancel_request_pending_until_finalization(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    candidate, report = _publication_ready(repository, run=run, owner_user_id=owner.id)
    requested = repository.cancel(
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )

    with_receipt = repository.record_publication_receipt(
        _publication_receipt(run=run, candidate=candidate, report=report)
    )

    assert requested.status == CodingRunStatus.CANCEL_REQUESTED
    assert with_receipt.status == CodingRunStatus.CANCEL_REQUESTED
    assert with_receipt.phase != CodingRunPhase.COMPLETED
    assert with_receipt.publication_receipt_id == "publication-receipt-1"
    assert (
        repository.events(
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
        )[-1].type
        == "publication.receipt_recorded_after_cancel_request"
    )

    partial_result = CodingPartialResult(
        artifact_ids=["artifact://cancelled"],
        summary="Cancellation activity retained partial output.",
        stopped_reason=CodingReasonCode.CANCELLED_BY_OWNER,
    )
    cancelled = repository.stop_with_partial_result(
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
        reason_code=CodingReasonCode.CANCELLED_BY_OWNER.value,
        partial_result=partial_result,
        cancelled=True,
    )

    assert cancelled.status == CodingRunStatus.CANCELLED
    assert cancelled.reason_code == CodingReasonCode.CANCELLED_BY_OWNER
    assert cancelled.partial_result == partial_result
    assert cancelled.publication_receipt_id == "publication-receipt-1"


@pytest.mark.parametrize(
    "status", [CodingRunStatus.FAILED, CodingRunStatus.REJECTED, CodingRunStatus.SUCCEEDED]
)
def test_late_publication_receipt_never_rewrites_another_terminal_run(setup, status) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    candidate, report = _publication_ready(repository, run=run, owner_user_id=owner.id)
    terminal = repository.transition_run(
        run_id=run.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
        phase=CodingRunPhase.COMPLETED,
        status=status,
        reason_code=CodingReasonCode.POLICY_DENIED.value,
        event_type="run.terminal_for_test",
    )

    persisted = repository.record_publication_receipt(
        _publication_receipt(run=run, candidate=candidate, report=report)
    )

    assert persisted.status == terminal.status == status
    assert persisted.reason_code == terminal.reason_code == CodingReasonCode.POLICY_DENIED


def _expire_budget_deadline(db: DatabaseRuntime, *, run_id: str) -> None:
    with db.session() as session:
        ledger = session.scalar(
            select(CodingBudgetLedgerOrm).where(CodingBudgetLedgerOrm.run_id == run_id)
        )
        assert ledger is not None
        ledger.deadline = datetime.now(tz=UTC) - timedelta(seconds=1)
        session.commit()


def test_developer_retry_after_reservation_deadline_does_not_reinvoke_executor(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    repository.create_task(_task(run_id=run.id, owner_user_id=owner.id))

    class Executor(CodingLifecycleExecutor):
        def __init__(self) -> None:
            self.developer_calls = 0

        async def prepare(self, *, admission):
            raise AssertionError("prepare is outside this retry test")

        async def develop(self, *, admission, task, operation_id, deadline):
            self.developer_calls += 1
            raise RuntimeError("temporary developer failure")

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            raise AssertionError("QA should not run")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publish should not run")

        async def cancel(self, *, admission):
            return ()

    executor = Executor()
    lifecycle = CodingLifecycleService(runs=repository, executor=executor)
    with pytest.raises(RuntimeError, match="temporary developer failure"):
        asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    _expire_budget_deadline(db, run_id=run.id)

    with pytest.raises(CodingBudgetExhausted, match="coding_runtime_limit_reached"):
        asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    assert executor.developer_calls == 1


def test_qa_retry_after_reservation_deadline_does_not_reinvoke_executor(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    qa = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )
    repository.record_candidate(_candidate(run=run))

    class Executor(CodingLifecycleExecutor):
        def __init__(self) -> None:
            self.qa_calls = 0

        async def prepare(self, *, admission):
            raise AssertionError("prepare is outside this retry test")

        async def develop(self, *, admission, task, operation_id, deadline):
            raise AssertionError("developer should not run")

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            assert task.id == qa.id
            self.qa_calls += 1
            raise RuntimeError("temporary QA failure")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publish should not run")

        async def cancel(self, *, admission):
            return ()

    executor = Executor()
    lifecycle = CodingLifecycleService(runs=repository, executor=executor)
    with pytest.raises(RuntimeError, match="temporary QA failure"):
        asyncio.run(lifecycle.qa(run_id=run.id, cycle=0))
    _expire_budget_deadline(db, run_id=run.id)

    with pytest.raises(CodingBudgetExhausted, match="coding_runtime_limit_reached"):
        asyncio.run(lifecycle.qa(run_id=run.id, cycle=0))
    assert executor.qa_calls == 1


def test_skill_read_checkpoint_is_preserved_before_developer_completion(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    developer = repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id, task_id="developer-task")
    )

    class Executor(CodingLifecycleExecutor):
        async def prepare(self, *, admission):
            raise AssertionError("prepare is outside this checkpoint test")

        async def develop(self, *, admission, task, operation_id, deadline):
            repository.checkpoint_task(
                task.model_copy(
                    update={
                        "skill_read_addresses": [
                            *task.skill_read_addresses,
                            SkillReadAddress(
                                skill_id="skill-1",
                                version_digest="digest-1",
                                path="rules/additional.md",
                                offset=0,
                                max_chars=1000,
                            ),
                        ]
                    }
                ),
                expected_version=task.version,
                event_type="skill.fragment_read",
            )
            return DeveloperExecution(
                candidate=_candidate(run=admission.run),
                checkpoint_ref="checkpoint://developer/0",
            )

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            raise AssertionError("QA should not run")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publish should not run")

        async def cancel(self, *, admission):
            return ()

    candidate = asyncio.run(
        CodingLifecycleService(runs=repository, executor=Executor()).develop(
            run_id=run.id,
            cycle=0,
        )
    )
    persisted = repository.get_task(
        task_id=developer.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )

    assert candidate.digest == "d" * 64
    assert persisted.status == CodingRunStatus.SUCCEEDED
    assert [address.path for address in persisted.skill_read_addresses] == [
        "SKILL.md",
        "rules/additional.md",
    ]


def test_skill_read_checkpoint_is_preserved_before_qa_completion(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    qa = repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )
    candidate = _candidate(run=run)
    repository.record_candidate(candidate)

    class Executor(CodingLifecycleExecutor):
        async def prepare(self, *, admission):
            raise AssertionError("prepare is outside this checkpoint test")

        async def develop(self, *, admission, task, operation_id, deadline):
            raise AssertionError("developer should not run")

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            repository.checkpoint_task(
                task.model_copy(
                    update={
                        "skill_read_addresses": [
                            *task.skill_read_addresses,
                            SkillReadAddress(
                                skill_id="skill-1",
                                version_digest="digest-1",
                                path="rules/qa.md",
                                offset=0,
                                max_chars=1000,
                            ),
                        ]
                    }
                ),
                expected_version=task.version,
                event_type="skill.fragment_read",
            )
            return QaExecution(
                verdict=QaVerdict(
                    id="qa-verdict-1",
                    qa_agent_id=task.agent_profile_id,
                    task_id=task.id,
                    candidate_digest=candidate.digest,
                    status="passed",
                    created_at=datetime.now(tz=UTC),
                ),
                evidence=(
                    ServerValidationEvidence(
                        id="qa-receipt-1",
                        workspace_id=admission.run.workspace_id,
                        owner_user_id=admission.run.owner_user_id,
                        run_id=admission.run.id,
                        task_id=task.id,
                        candidate_digest=candidate.digest,
                        name="dbt_compile",
                        source="sandbox",
                        status="passed",
                        exit_code=0,
                        duration_ms=10,
                        created_at=datetime.now(tz=UTC),
                    ),
                ),
                checkpoint_ref="checkpoint://qa/0",
            )

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publish should not run")

        async def cancel(self, *, admission):
            return ()

    report = asyncio.run(
        CodingLifecycleService(runs=repository, executor=Executor()).qa(
            run_id=run.id,
            cycle=0,
        )
    )
    persisted = repository.get_task(
        task_id=qa.id,
        workspace_id=run.workspace_id,
        owner_user_id=run.owner_user_id,
    )

    assert report.candidate_digest == candidate.digest
    assert persisted.status == CodingRunStatus.SUCCEEDED
    assert [address.path for address in persisted.skill_read_addresses] == [
        "SKILL.md",
        "rules/qa.md",
    ]


def test_lifecycle_recovers_candidate_and_qa_before_non_mutating_publication(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    repository.create_task(_task(run_id=run.id, owner_user_id=owner.id, task_id="developer-task"))
    repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )

    class Executor(CodingLifecycleExecutor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def prepare(self, *, admission):
            self.calls.append("prepare")

        async def develop(self, *, admission, task, operation_id, deadline):
            self.calls.append(operation_id)
            return DeveloperExecution(
                candidate=_candidate(run=admission.run),
                checkpoint_ref="checkpoint://developer/0",
                artifact_ids=("artifact://candidate-1",),
            )

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            self.calls.append(operation_id)
            return QaExecution(
                verdict=QaVerdict(
                    id="qa-verdict-1",
                    qa_agent_id=task.agent_profile_id,
                    task_id=task.id,
                    candidate_digest=candidate.digest,
                    status="passed",
                    created_at=datetime.now(tz=UTC),
                ),
                evidence=(
                    ServerValidationEvidence(
                        id="qa-receipt-1",
                        workspace_id=admission.run.workspace_id,
                        owner_user_id=admission.run.owner_user_id,
                        run_id=admission.run.id,
                        task_id=task.id,
                        candidate_digest=candidate.digest,
                        name="dbt_compile",
                        source="sandbox",
                        status="passed",
                        exit_code=0,
                        duration_ms=10,
                        created_at=datetime.now(tz=UTC),
                    ),
                ),
                checkpoint_ref="checkpoint://qa/0",
            )

        async def publish(self, *, admission, candidate, report):
            self.calls.append("publish")
            raise PublicationDenied(CodingReasonCode.PUBLICATION_NOT_AUTHORIZED)

        async def cancel(self, *, admission):
            self.calls.append("cancel")
            return ()

    executor = Executor()
    lifecycle = CodingLifecycleService(runs=repository, executor=executor)

    prepared = asyncio.run(lifecycle.prepare(run_id=run.id))
    first_candidate = asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    recovered_candidate = asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    first_report = asyncio.run(lifecycle.qa(run_id=run.id, cycle=0))
    recovered_report = asyncio.run(lifecycle.qa(run_id=run.id, cycle=0))
    completed = asyncio.run(lifecycle.publish(run_id=run.id))

    assert prepared.phase == CodingRunPhase.DEVELOPING
    assert recovered_candidate == first_candidate
    assert recovered_report == first_report
    assert executor.calls == ["prepare", "developer:0", "qa:0", "publish"]
    assert completed.status == CodingRunStatus.SUCCEEDED
    assert completed.reason_code == CodingReasonCode.PUBLICATION_NOT_AUTHORIZED


def test_lifecycle_retries_failed_qa_with_existing_developer_budget(setup) -> None:
    """A QA fix cycle gets a new candidate, not a new durable developer identity."""

    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(
        _admission(run_id=str(uuid4()), owner_user_id=owner.id, max_iterations=4)
    ).run
    repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="developer-task",
            max_iterations=4,
        )
    )
    repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
            max_iterations=4,
        )
    )

    class Executor(CodingLifecycleExecutor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def prepare(self, *, admission):
            self.calls.append("prepare")

        async def develop(self, *, admission, task, operation_id, deadline):
            self.calls.append(operation_id)
            cycle = operation_id.rsplit(":", maxsplit=1)[1]
            return DeveloperExecution(
                candidate=_candidate(run=admission.run, digest=f"{cycle}" * 64),
                checkpoint_ref=f"checkpoint://developer/{cycle}",
            )

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            self.calls.append(operation_id)
            cycle = operation_id.rsplit(":", maxsplit=1)[1]
            return QaExecution(
                verdict=QaVerdict(
                    id=f"qa-verdict-{cycle}",
                    qa_agent_id=task.agent_profile_id,
                    task_id=task.id,
                    candidate_digest=candidate.digest,
                    status="failed" if cycle == "0" else "passed",
                    created_at=datetime.now(tz=UTC),
                ),
                evidence=(
                    ServerValidationEvidence(
                        id=f"qa-receipt-{cycle}",
                        workspace_id=admission.run.workspace_id,
                        owner_user_id=admission.run.owner_user_id,
                        run_id=admission.run.id,
                        task_id=task.id,
                        candidate_digest=candidate.digest,
                        name="dbt_compile",
                        source="sandbox",
                        status="passed",
                        exit_code=0,
                        duration_ms=10,
                        created_at=datetime.now(tz=UTC),
                    ),
                ),
                checkpoint_ref=f"checkpoint://qa/{cycle}",
            )

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publication is outside this recovery test")

        async def cancel(self, *, admission):
            return ()

    executor = Executor()
    lifecycle = CodingLifecycleService(runs=repository, executor=executor)

    asyncio.run(lifecycle.prepare(run_id=run.id))
    first_candidate = asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    first_report = asyncio.run(lifecycle.qa(run_id=run.id, cycle=0))
    second_candidate = asyncio.run(lifecycle.develop(run_id=run.id, cycle=1))
    second_report = asyncio.run(lifecycle.qa(run_id=run.id, cycle=1))

    assert first_report.qa_verdict is not None
    assert first_report.qa_verdict.status == "failed"
    assert second_candidate.digest != first_candidate.digest
    assert second_report.candidate_digest == second_candidate.digest
    assert executor.calls == ["prepare", "developer:0", "qa:0", "developer:1", "qa:1"]
    ledgers = repository.budget_ledgers(
        run_id=run.id,
        workspace_id="default",
        owner_user_id=owner.id,
    )
    assert {(ledger.budget_owner_agent_id, ledger.reserved_iterations) for ledger in ledgers} == {
        ("manager-1", 4),
    }


def test_lifecycle_stops_before_an_unreserved_final_model_call(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(
        _admission(run_id=str(uuid4()), owner_user_id=owner.id, max_iterations=1)
    ).run
    repository.create_task(
        _task(run_id=run.id, owner_user_id=owner.id, task_id="developer-task").model_copy(
            update={"max_iterations": 1}
        )
    )
    repository.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="qa-task",
            agent_profile_id="qa-1",
            role="qa",
        )
    )

    class Executor(CodingLifecycleExecutor):
        def __init__(self) -> None:
            self.developer_calls: list[str] = []

        async def prepare(self, *, admission):
            return None

        async def develop(self, *, admission, task, operation_id, deadline):
            self.developer_calls.append(operation_id)
            return DeveloperExecution(
                candidate=_candidate(run=admission.run),
                checkpoint_ref="checkpoint://developer/0",
            )

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            raise AssertionError("QA should not run")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publication should not run")

        async def cancel(self, *, admission):
            return ("artifact://partial",)

    executor = Executor()
    lifecycle = CodingLifecycleService(runs=repository, executor=executor)
    asyncio.run(lifecycle.prepare(run_id=run.id))
    asyncio.run(lifecycle.develop(run_id=run.id, cycle=0))
    with pytest.raises(CodingBudgetExhausted, match="coding_iteration_limit_reached") as error:
        asyncio.run(lifecycle.develop(run_id=run.id, cycle=1))

    stopped = asyncio.run(lifecycle.stop_after_budget_or_deadline(run_id=run.id, error=error.value))

    assert executor.developer_calls == ["developer:0"]
    assert stopped.status == CodingRunStatus.FAILED
    assert stopped.reason_code == CodingReasonCode.ITERATION_LIMIT_REACHED
    assert stopped.partial_result is not None
    assert stopped.partial_result.artifact_ids == ["artifact://partial"]


def test_lifecycle_persists_owner_cancellation_when_remote_stop_fails(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run

    class Executor(CodingLifecycleExecutor):
        async def prepare(self, *, admission):
            return None

        async def develop(self, *, admission, task, operation_id, deadline):
            raise AssertionError("developer should not run")

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            raise AssertionError("QA should not run")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publication should not run")

        async def cancel(self, *, admission):
            raise TimeoutError("sandbox request timed out")

    cancelled = asyncio.run(
        CodingLifecycleService(runs=repository, executor=Executor()).cancel(run_id=run.id)
    )

    assert cancelled.status == CodingRunStatus.CANCELLED
    assert cancelled.reason_code == CodingReasonCode.CANCELLED_BY_OWNER
    assert cancelled.partial_result is not None
    assert cancelled.partial_result.artifact_ids == []


def test_activity_failure_persists_partial_result_before_workflow_exits(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run

    class Executor(CodingLifecycleExecutor):
        async def prepare(self, *, admission):
            raise AssertionError("prepare should not run")

        async def develop(self, *, admission, task, operation_id, deadline):
            raise AssertionError("developer should not run")

        async def qa(self, *, admission, task, candidate, operation_id, deadline):
            raise AssertionError("QA should not run")

        async def publish(self, *, admission, candidate, report):
            raise AssertionError("publish should not run")

        async def cancel(self, *, admission):
            return ("artifact://partial",)

    payload = asyncio.run(
        CodingActivities(CodingLifecycleService(runs=repository, executor=Executor())).fail(run.id)
    )
    persisted = repository.get_admission_for_worker(run_id=run.id).run

    assert payload == {
        "terminal": True,
        "status": CodingRunStatus.FAILED,
        "reason_code": CodingReasonCode.EXECUTION_FAILED,
    }
    assert persisted.status == CodingRunStatus.FAILED
    assert persisted.reason_code == CodingReasonCode.EXECUTION_FAILED
    assert persisted.partial_result is not None
    assert persisted.partial_result.artifact_ids == ["artifact://partial"]


def test_workflow_finalizes_unhandled_activity_error_before_reraising(monkeypatch) -> None:
    calls: list[str] = []

    async def activity(
        name,
        _argument,
        *,
        activity_id,
        long_running=False,
        retry=True,
    ):
        del activity_id, long_running, retry
        calls.append(name)
        if name == "coding_prepare":
            return {"terminal": False}
        if name == "coding_develop":
            raise RuntimeError("activity retries exhausted")
        assert name == "coding_fail"
        return {"terminal": True, "status": "failed"}

    monkeypatch.setattr(CodingWorkflow, "_activity", staticmethod(activity))
    monkeypatch.setattr(CodingWorkflow, "_teams_enabled", staticmethod(lambda: False))

    with pytest.raises(RuntimeError, match="activity retries exhausted"):
        asyncio.run(CodingWorkflow().run("run-1"))

    assert calls == ["coding_prepare", "coding_develop", "coding_fail"]


def test_workflow_finalizes_cancel_request_returned_by_publication(monkeypatch) -> None:
    calls: list[str] = []

    async def activity(
        name,
        _argument,
        *,
        activity_id,
        long_running=False,
        retry=True,
    ):
        del activity_id, long_running, retry
        calls.append(name)
        responses = {
            "coding_prepare": {"terminal": False},
            "coding_develop": {"terminal": False},
            "coding_qa": {"terminal": False, "retry_developer": False},
            "coding_publish": {"terminal": False, "status": "cancel_requested"},
            "coding_cancel": {"terminal": True, "status": "cancelled"},
        }
        return responses[name]

    monkeypatch.setattr(CodingWorkflow, "_activity", staticmethod(activity))
    monkeypatch.setattr(CodingWorkflow, "_teams_enabled", staticmethod(lambda: False))

    asyncio.run(CodingWorkflow().run("run-1"))

    assert calls == [
        "coding_prepare",
        "coding_develop",
        "coding_qa",
        "coding_publish",
        "coding_cancel",
    ]


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        (
            {"terminal": False, "publish": True},
            ["coding_prepare", "coding_coordinate", "coding_publish"],
        ),
        ({"terminal": True, "publish": False}, ["coding_prepare", "coding_coordinate"]),
    ],
)
def test_team_workflow_runs_one_coordinator_turn_before_server_publication(
    monkeypatch, turn, expected
) -> None:
    calls: list[str] = []
    arguments: dict[str, object] = {}

    async def activity(name, argument, *, activity_id, long_running=False, retry=True, **_):
        del activity_id, long_running, retry
        calls.append(name)
        arguments[name] = argument
        responses = {
            "coding_prepare": {"terminal": False},
            "coding_coordinate": turn,
            "coding_publish": {"terminal": True, "status": "succeeded"},
        }
        return responses[name]

    monkeypatch.setattr(CodingWorkflow, "_activity", staticmethod(activity))
    monkeypatch.setattr(CodingWorkflow, "_teams_enabled", staticmethod(lambda: True))

    asyncio.run(CodingWorkflow().run("run-1"))

    assert calls == expected
    assert arguments["coding_coordinate"] == {"run_id": "run-1", "turn": 0}


def test_progress_cursor_is_owner_scoped_and_returns_only_references(setup) -> None:
    db, owner, other = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    repository.create_task(_task(run_id=run.id, owner_user_id=owner.id))
    reader = CodingRunProgressReader(repository)

    first = reader.read(
        run_id=run.id,
        workspace_id="default",
        owner_user_id=owner.id,
    )
    resumed = reader.read(
        run_id=run.id,
        workspace_id="default",
        owner_user_id=owner.id,
        after=first.cursor,
    )

    assert [event.sequence for event in first.events] == list(range(1, first.cursor + 1))
    assert resumed.events == []
    assert resumed.cursor == first.cursor
    assert first.artifact_ids == ["artifact://instruction-1"]
    with pytest.raises(CodingRunNotFound):
        reader.read(
            run_id=run.id,
            workspace_id="default",
            owner_user_id=other.id,
        )


def test_outbox_duplicate_start_delivery_uses_stable_workflow_id(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run

    class Client:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.workflow_ids: list[str] = []

        async def start_workflow(self, _run, _run_id, **kwargs) -> None:
            self.workflow_ids.append(kwargs["id"])
            self.started.set()

        def get_workflow_handle(self, _workflow_id):
            raise AssertionError("cancel was not queued")

    async def deliver_once() -> Client:
        client = Client()
        task = asyncio.create_task(
            dispatch_coding_outbox(
                client=client,  # type: ignore[arg-type]
                repository=repository,
                settings=CodingWorkerSettings(),
                poll_interval_seconds=10,
            )
        )
        await asyncio.wait_for(client.started.wait(), timeout=1)
        # Stop only after the durable delivery mark: cancelling mid-write races the assertion.
        for _ in range(100):
            if not await asyncio.to_thread(repository.pending_outbox):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return client

    client = asyncio.run(deliver_once())

    assert client.workflow_ids == [f"coding/{run.id}"]
    assert repository.pending_outbox() == []


def test_delegation_uses_web_principal_and_live_parent_allowlist(setup) -> None:
    db, owner, _ = setup
    profiles = AgentProfileRepository(db)
    manager = profiles.create(
        UpsertAgentProfileRequest(workspace_id="default", slug="manager", title="Manager"),
        actor_user_id=owner.id,
    )
    developer = profiles.create(
        UpsertAgentProfileRequest(workspace_id="default", slug="developer", title="Developer"),
        actor_user_id=owner.id,
    )
    policies = AgentPolicyRepository(db)
    policies.replace_agent_policy(
        workspace_id="default",
        agent_id=manager.id,
        actor_user_id=owner.id,
        settings=GovernedAgentSettings(
            revision=1,
            allowed_subagent_ids=[developer.id],
            max_iterations=100,
            max_runtime_seconds=1200,
        ),
    )
    policies.replace_agent_policy(
        workspace_id="default",
        agent_id=developer.id,
        actor_user_id=owner.id,
        settings=GovernedAgentSettings(
            revision=1,
            max_iterations=1,
            max_runtime_seconds=30,
        ),
    )
    runs = CodingRunRepository(db)
    run = runs.admit(
        _admission(
            run_id=str(uuid4()),
            owner_user_id=owner.id,
            max_iterations=100,
            max_runtime_seconds=1200,
        )
    ).run
    parent = runs.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            task_id="parent",
            agent_profile_id=manager.id,
        )
    )
    service = CodingDelegationService(
        runs=runs,
        users=UserAccountRepository(db),
        workspaces=WorkspaceRepository(db),
        agents=profiles,
        policies=policies,
    )
    principal = ExecutionPrincipal(
        actor_user_id=owner.id,
        workspace_id="default",
        entry_source="web_scenario",
        run_id=run.id,
        task_id=parent.id,
        root_agent_id=manager.id,
        executing_agent_id=manager.id,
    )
    request = CreateCodingDelegationRequest(
        run_id=run.id,
        parent_task_id=parent.id,
        child_agent_profile_id=developer.id,
        role="developer",
        instruction_artifact_id="artifact://delegated-instruction",
    )

    child = service.delegate(principal=principal, request=request)

    assert child.parent_task_id == parent.id
    assert child.agent_profile_id == developer.id
    assert child.max_iterations == 1
    assert child.max_runtime_seconds == 30
    nested_principal = principal.model_copy(
        update={"task_id": child.id, "executing_agent_id": child.agent_profile_id}
    )
    nested_request = request.model_copy(update={"parent_task_id": child.id})
    with pytest.raises(CodingDelegationDenied, match="coding_delegation_depth_exceeded"):
        service.delegate(principal=nested_principal, request=nested_request)


def test_agents_sdk_tools_revalidate_live_access_and_reject_generic_input(setup) -> None:
    db, owner, _ = setup
    profiles = AgentProfileRepository(db)
    manager = profiles.create(
        UpsertAgentProfileRequest(workspace_id="default", slug="manager", title="Manager"),
        actor_user_id=owner.id,
    )
    runs = CodingRunRepository(db)
    run = runs.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    task = runs.create_task(
        _task(
            run_id=run.id,
            owner_user_id=owner.id,
            agent_profile_id=manager.id,
            role="developer",
        )
    )
    principal = ExecutionPrincipal(
        actor_user_id=owner.id,
        workspace_id="default",
        entry_source="web_scenario",
        root_agent_id=manager.id,
        executing_agent_id=manager.id,
        run_id=run.id,
        task_id=task.id,
    )

    class Gateway:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def invoke(self, *, name, task, principal, arguments):
            self.calls.append(name)
            return {"task_id": task.id, "ok": True}

    gateway = Gateway()
    factory = CodingSpecialistAgentFactory(
        authorizer=CodingToolAuthorizationService(
            runs=runs,
            users=UserAccountRepository(db),
            workspaces=WorkspaceRepository(db),
            agents=profiles,
        ),
        gateway=gateway,
    )
    agent = factory.build(
        capability=CodingTeamMemberCapability.CANDIDATE,
        principal=principal,
        model="test-model",
        timeout_seconds=10,
    )
    status_tool = next(tool for tool in agent.tools if tool.name == "task_status")

    assert asyncio.run(status_tool.on_invoke_tool(None, "{}")) == '{"ok":true,"task_id":"task-1"}'
    assert gateway.calls == ["task_status"]
    assert "invoke_tool" not in [tool.name for tool in agent.tools]
    assert "delegate_task" not in [tool.name for tool in agent.tools]
    malformed = asyncio.run(status_tool.on_invoke_tool(None, '{"workspace_id": "foreign"}'))
    assert '"status": "invalid_arguments"' in malformed
    assert gateway.calls == ["task_status"]

    profiles.update(
        manager.id,
        "default",
        UpsertAgentProfileRequest(
            workspace_id="default",
            slug="manager",
            title="Manager",
            is_active=False,
        ),
        actor_user_id=owner.id,
    )
    with pytest.raises(CodingToolAccessDenied, match="coding_tool_agent_unavailable"):
        asyncio.run(status_tool.on_invoke_tool(None, "{}"))
    assert gateway.calls == ["task_status"]


def test_skill_reader_keeps_bound_version_and_rechecks_live_access(setup) -> None:
    db, owner, _ = setup
    skills = WorkspaceSkillRepository(db)
    initial = skills.create(
        workspace_id="default",
        name="coding-guide",
        capability_family="chat_instruction",
        engine_scope="any",
        priority=100,
        source_kind="manual_markdown",
        source_url=None,
        status="active",
        validation_status="valid",
        visible_to_default_assistant=True,
        title="Guide",
        summary="Initial guide",
        content_markdown="first-version-content",
        content_bundle={
            "assets": [
                {
                    "path": "SKILL.md",
                    "kind": "main",
                    "content_markdown": "first-version-content",
                },
                {
                    "path": "reference/cosmos-config.md",
                    "kind": "doc",
                    "title": "Cosmos configuration",
                    "summary": "Configure Cosmos for this dbt project.",
                    "content_markdown": "adapter_type: trino\nexecution_mode: local\n",
                },
            ]
        },
        content_sha256="a" * 64,
        version_digest="version-1",
        last_validated_at=datetime.now(tz=UTC),
    )
    profiles = AgentProfileRepository(db)
    developer = profiles.create(
        UpsertAgentProfileRequest(
            workspace_id="default",
            slug="developer",
            title="Developer",
            allowed_workspace_skill_ids=[initial.id],
        ),
        actor_user_id=owner.id,
    )
    runs = CodingRunRepository(db)
    run = runs.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    task = runs.create_task(
        _task(run_id=run.id, owner_user_id=owner.id, agent_profile_id=developer.id).model_copy(
            update={
                "skill_version_digests": ["version-1"],
                "skill_version_bindings": [
                    WorkspaceSkillVersionBinding(
                        skill_id=initial.id,
                        version_digest="version-1",
                    )
                ],
                "skill_read_addresses": [],
            }
        )
    )
    principal = ExecutionPrincipal(
        actor_user_id=owner.id,
        workspace_id="default",
        entry_source="web_scenario",
        executing_agent_id=developer.id,
        run_id=run.id,
        task_id=task.id,
    )
    reader = CodingWorkspaceSkillReader(
        runs=runs,
        users=UserAccountRepository(db),
        workspaces=WorkspaceRepository(db),
        agents=profiles,
        skills=skills,
    )

    fragment = reader.read(
        principal=principal,
        skill_id=initial.id,
        version_digest="version-1",
        path="SKILL.md",
        max_chars=5,
    )
    assert fragment.content == "first"
    assert [entry.model_dump() for entry in fragment.available_files or []] == [
        {
            "path": "SKILL.md",
            "kind": "main",
            "size_chars": len("first-version-content"),
            "title": None,
            "summary": None,
        },
        {
            "path": "reference/cosmos-config.md",
            "kind": "doc",
            "size_chars": len("adapter_type: trino\nexecution_mode: local\n"),
            "title": "Cosmos configuration",
            "summary": "Configure Cosmos for this dbt project.",
        },
    ]
    reference = reader.read(
        principal=principal,
        skill_id=initial.id,
        version_digest="version-1",
        path="reference/cosmos-config.md",
        max_chars=8,
    )
    assert reference.content == "adapter_"
    assert reference.truncated and reference.next_offset == 8
    assert reference.available_files is None
    assert runs.get_task(
        task_id=task.id,
        workspace_id="default",
        owner_user_id=owner.id,
    ).skill_read_addresses
    skills.update(
        skill_id=initial.id,
        workspace_id="default",
        name="coding-guide",
        capability_family="chat_instruction",
        engine_scope="any",
        priority=100,
        source_kind="manual_markdown",
        source_url=None,
        status="active",
        validation_status="valid",
        visible_to_default_assistant=True,
        title="Guide",
        summary="Updated guide",
        content_markdown="second-version-content",
        content_bundle={
            "assets": [
                {
                    "path": "SKILL.md",
                    "kind": "main",
                    "content_markdown": "second-version-content",
                }
            ]
        },
        content_sha256="b" * 64,
        version_digest="version-2",
        last_validated_at=datetime.now(tz=UTC),
    )
    continued = reader.read(
        principal=principal,
        skill_id=initial.id,
        version_digest="version-1",
        path="SKILL.md",
        offset=6,
        max_chars=7,
    )
    assert continued.content == "version"
    assert continued.available_files is None
    profiles.update(
        developer.id,
        "default",
        UpsertAgentProfileRequest(
            workspace_id="default",
            slug="developer",
            title="Developer",
            allowed_workspace_skill_ids=[],
        ),
        actor_user_id=owner.id,
    )
    with pytest.raises(CodingToolAccessDenied, match="coding_skill_reader_access_revoked"):
        reader.read(
            principal=principal,
            skill_id=initial.id,
            version_digest="version-1",
            path="SKILL.md",
        )


def test_cancellation_and_outbox_acknowledgement_are_idempotent(setup) -> None:
    db, owner, _ = setup
    repository = CodingRunRepository(db)
    run = repository.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    repository.cancel(run_id=run.id, workspace_id="default", owner_user_id=owner.id)
    repository.cancel(run_id=run.id, workspace_id="default", owner_user_id=owner.id)

    entries = repository.pending_outbox()
    cancel = next(entry for entry in entries if entry.kind == "cancel")
    delivered = repository.record_outbox_attempt(entry=cancel, delivered=True)
    assert delivered is not None and delivered.delivered is True
    assert repository.record_outbox_attempt(entry=cancel, delivered=True) is None
    assert [entry.kind for entry in repository.pending_outbox()] == ["start"]


def test_admission_commits_initial_tree_before_outbox_dispatch(setup):
    db, owner, _other = setup
    repository = CodingRunRepository(db)
    admission = _admission(run_id=str(uuid4()), owner_user_id=owner.id)
    parent = _task(
        run_id=admission.run.id, owner_user_id=owner.id, task_id="root", role="coordinator"
    )
    child = _task(run_id=admission.run.id, owner_user_id=owner.id, task_id="dev").model_copy(
        update={"parent_task_id": "root"}
    )
    first = repository.admit(admission, tasks=[parent, child])
    duplicate = repository.admit(admission, tasks=[parent, child])
    assert first.created and not duplicate.created
    assert {
        t.id
        for t in repository.tasks(
            run_id=admission.run.id, workspace_id="default", owner_user_id=owner.id
        )
    } == {"root", "dev"}


def test_specialist_chat_events_are_owner_scoped_durable_and_idempotent(setup):
    db, owner, other = setup
    repository = CodingRunRepository(db)
    admission = _admission(run_id="chat-run", owner_user_id=owner.id)
    repository.admit(admission, tasks=[_task(run_id="chat-run", owner_user_id=owner.id)])
    scope = {
        "run_id": "chat-run",
        "task_id": "task-1",
        "workspace_id": "default",
        "owner_user_id": owner.id,
    }
    repository.record_chat_event(
        **scope,
        event_type="agent.tool_started",
        event_key="call-1",
        payload={"tool_name": "repository_read", "tool_call_id": "call-1"},
    )
    repository.record_chat_event(
        **scope,
        event_type="agent.tool_started",
        event_key="call-1",
        payload={"tool_name": "repository_read", "tool_call_id": "call-1"},
    )
    repository.record_chat_event(
        **scope, event_type="agent.tool_completed", event_key="call-1", payload={"success": True}
    )
    events = CodingRunRepository(db).events(
        run_id="chat-run", workspace_id="default", owner_user_id=owner.id
    )
    chat = [event for event in events if event.type.startswith("agent.tool_")]
    assert [event.type for event in chat] == ["agent.tool_started", "agent.tool_completed"]
    assert chat[0].sequence < chat[1].sequence
    assert (
        repository.get_task(
            task_id="task-1", workspace_id="default", owner_user_id=owner.id
        ).iterations_used
        == 0
    )
    with pytest.raises(CodingRunNotFound):
        repository.record_chat_event(
            **{**scope, "owner_user_id": other.id},
            event_type="agent.message",
            event_key="message",
            payload={"content": "no"},
        )
    with pytest.raises(CodingRunConflict, match="type_invalid"):
        repository.record_chat_event(
            **scope, event_type="run.succeeded", event_key="fake", payload={}
        )


def test_task_sessions_share_store_but_keep_owner_workspace_and_specialist_isolation(setup):
    from rhetorica_backend.infra.postgres.agent_session import (
        AgentSessionConversationNotFoundError,
        PostgresAgentSessionStore,
    )

    db, owner, other = setup
    runs = CodingRunRepository(db)
    run = runs.admit(_admission(run_id=str(uuid4()), owner_user_id=owner.id)).run
    first = runs.create_task(_task(run_id=run.id, owner_user_id=owner.id))
    second = runs.create_task(
        _task(run_id=run.id, owner_user_id=owner.id).model_copy(
            update={"id": "task-2", "role": "qa"}
        )
    )
    store = PostgresAgentSessionStore(db, owner.id, workspace_id="default")
    session = store.for_task(first.id)
    asyncio.run(session.add_items([{"role": "user", "content": "developer history"}]))
    assert asyncio.run(store.for_task(first.id).get_items())[0]["content"] == "developer history"
    assert asyncio.run(store.for_task(second.id).get_items()) == []
    for owner_id, workspace in [
        (other.id, "default"),
        (owner.id, "foreign"),
        (None, "default"),
        (owner.id, None),
    ]:
        denied = PostgresAgentSessionStore(db, owner_id, workspace_id=workspace)
        with pytest.raises(AgentSessionConversationNotFoundError):
            denied.for_task(first.id)
        with pytest.raises(AgentSessionConversationNotFoundError):
            denied.get_items(first.id)


def test_coding_usage_is_owner_scoped_and_updates_chat_exactly_once(setup):
    from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm
    from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
    from rhetorica_backend.infra.postgres.coding_run_models import CodingIterationReservationOrm

    db, owner, other = setup
    chat = ChatRepository(db).create_conversation(
        workspace_id="default", title="Usage", owner_user_id=owner.id
    )
    admission = _admission(run_id=str(uuid4()), owner_user_id=owner.id)
    admission = admission.model_copy(
        update={"run": admission.run.model_copy(update={"conversation_id": chat.id})}
    )
    runs = CodingRunRepository(db)
    run = runs.admit(admission).run
    task = runs.create_task(_task(run_id=run.id, owner_user_id=owner.id))
    scope = {
        "run_id": run.id,
        "task_id": task.id,
        "invocation_id": "model-call",
        "workspace_id": "default",
        "owner_user_id": owner.id,
    }
    runs.reserve_iteration(**scope, executing_agent_profile_id=task.agent_profile_id)
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 70,
        "cache_write_input_tokens": 0,
        "cache_usage_reported_calls": 1,
        "total_tokens": 120,
        "usage_reported": True,
    }
    runs.record_model_usage(**scope, usage=usage)
    runs.record_model_usage(**scope, usage=usage)
    with db.session() as session:
        saved = session.get(ChatConversationOrm, chat.id)
        assert (saved.llm_input_tokens, saved.llm_output_tokens, saved.llm_cached_input_tokens) == (
            100,
            20,
            70,
        )
        receipt = session.scalar(
            select(CodingIterationReservationOrm).where(
                CodingIterationReservationOrm.run_id == run.id
            )
        )
        assert receipt.task_id == task.id and receipt.usage_json == usage
    with pytest.raises(CodingRunNotFound):
        runs.record_model_usage(**{**scope, "owner_user_id": other.id}, usage=usage)
