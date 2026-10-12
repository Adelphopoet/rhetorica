"""Concrete lifecycle adapter composed from SDK, runner, snapshots and publisher."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from rhetorica_backend.contracts.coding import (
    QaVerdict,
    ServerValidationEvidence,
)
from rhetorica_backend.domain.coding.lifecycle import (
    CodingLifecycleExecutor,
    DeveloperExecution,
    QaExecution,
)
from rhetorica_backend.domain.coding.publication_diff import tree_from_candidate_archive
from rhetorica_backend.domain.coding.qa_verdict import QaResult
from rhetorica_backend.infra.coding.repository_snapshot import TrustedRepositorySnapshotService

if TYPE_CHECKING:
    from rhetorica_backend.domain.coding.service import CodingRunService
    from rhetorica_backend.domain.coding.settings import CodingSettings
    from rhetorica_backend.domain.coding.specialist_runtime import CodingSpecialistRuntime
    from rhetorica_backend.infra.coding.gitlab_publisher import GitLabPublicationService
    from rhetorica_backend.infra.coding.local_runner import CodingRunnerPort


class GovernedCodingExecutor(CodingLifecycleExecutor):
    def __init__(
        self,
        *,
        service: CodingRunService,
        runner: CodingRunnerPort,
        publisher: GitLabPublicationService,
        settings: CodingSettings,
        agents: CodingSpecialistRuntime,
        coordinator=None,
    ):
        self.service = service
        self.runner = runner
        self.publisher = publisher
        self.settings = settings
        self.snapshots = TrustedRepositorySnapshotService()
        self.agents = agents
        self.skills = agents.skills
        self.coordinator = coordinator

    def _tasks(self, admission):
        r = admission.run
        return self.service.runs.tasks(
            run_id=r.id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id
        )

    def _artifact(self, admission, ref, task_id):
        r = admission.run
        return self.service.artifacts.download_owned(
            artifact_id=ref,
            workspace_id=r.workspace_id,
            owner_user_id=r.owner_user_id,
            run_id=r.id,
            task_id=task_id,
        )[0]

    def _producer_task_id(self, candidate):
        """Any candidate-capable member may own a candidate; its signed scope names it."""

        return str(self.service.artifacts.scope(candidate.artifact_id)["task_id"])

    def _persist(self, admission, task_id, payload, kind):
        r = admission.run
        return self.service.artifacts.persist(
            payload=payload,
            digest=hashlib.sha256(payload).hexdigest(),
            workspace_id=r.workspace_id,
            owner_user_id=r.owner_user_id,
            run_id=r.id,
            task_id=task_id,
            kind=kind,
        )

    async def prepare(self, *, admission):
        coordinator = next(t for t in self._tasks(admission) if t.role == "coordinator")
        if coordinator.checkpoint_ref:
            return
        r = admission.run
        connection = self.service.gitlab_connectors.get_connection_by_id(
            workspace_id=r.workspace_id, connector_id=r.repository.gitlab_connector_id
        )
        if connection is None:
            raise PermissionError("resource_access_revoked")
        snapshot = await asyncio.to_thread(
            self.snapshots.prepare, connection=connection, run=r, principal=admission.principal
        )
        ref = await asyncio.to_thread(
            self._persist, admission, coordinator.id, snapshot.payload, "base"
        )
        self.service.runs.checkpoint_task(
            coordinator.model_copy(update={"checkpoint_ref": ref, "status": "succeeded"}),
            expected_version=coordinator.version,
            event_type="repository.prepared",
        )

    async def _prepare_environment(self, admission, task, deadline):
        profile = admission.environment_profile
        if not profile.pip_dependencies:
            return ()
        remaining = int((deadline.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted

            raise CodingBudgetExhausted("coding_runtime_limit_reached")
        operation = f"python-environment:{profile.id}:{profile.revision}"
        try:
            receipt = await asyncio.to_thread(
                self.runner.prepare_environment, task.id, profile, min(300, remaining)
            )
        except httpx.HTTPError:
            await asyncio.to_thread(
                self.service.journal.put,
                run_id=admission.run.id,
                task_id=task.id,
                operation_id=operation,
                value={
                    "status": "failed",
                    "reason_code": "coding_dependency_install_failed",
                    "profile_id": profile.id,
                    "profile_revision": profile.revision,
                },
            )
            from rhetorica_backend.domain.coding.errors import CodingEnvironmentPreparationFailed

            raise CodingEnvironmentPreparationFailed() from None
        await asyncio.to_thread(
            self.service.journal.put,
            run_id=admission.run.id,
            task_id=task.id,
            operation_id=operation,
            value=receipt,
        )
        from rhetorica_backend.domain.coding.environment_receipt import python_environment_archive

        payload = python_environment_archive(receipt)
        ref = await asyncio.to_thread(
            self._persist, admission, task.id, payload, "python-environment"
        )
        return (ref,)

    async def develop(self, *, admission, task, operation_id, deadline, assignment=""):
        r = admission.run
        coordinator = next(t for t in self._tasks(admission) if t.role == "coordinator")
        base = await asyncio.to_thread(
            self._artifact, admission, coordinator.checkpoint_ref, coordinator.id
        )
        staged = (
            await asyncio.to_thread(
                self._artifact,
                admission,
                r.candidate.artifact_id,
                self._producer_task_id(r.candidate),
            )
            if r.candidate
            else base
        )
        retained = await asyncio.to_thread(self.runner.stage, task.id, staged)
        preparation_artifacts = await self._prepare_environment(admission, task, deadline)
        feedback = ""
        if r.validation_report_id:
            report = self.service.runs.validation_report(
                report_id=r.validation_report_id,
                run_id=r.id,
                workspace_id=r.workspace_id,
                owner_user_id=r.owner_user_id,
            )
            feedback = "\nFix these QA findings: " + json.dumps(report.model_dump(mode="json"))
        output = await self.agents.run(
            admission,
            task,
            operation_id,
            deadline,
            "Implement the user's task through completion. Start with repository guidance and "
            "the smallest relevant set of files; stop exploring once the edit is clear. "
            "Match effort to scope: a self-contained file addition needs local conventions "
            "and targeted checks, not unrelated warehouse discovery or project redesign. "
            "After editing, inspect git diff and run targeted checks. Distinguish passed, failed, "
            "blocked and not_run; repair your errors using tool diagnostics. "
            "Once repository guidance and one relevant example are known, make the edit; "
            "do not search unrelated tests, docs or directories for a small change. "
            "Apply patches as unified git diffs with a/ and b/ paths. "
            "Never materialize warehouse data or modify CI, secrets, credentials, .git or generated exports. "
            "Use run_dbt for dbt; dbt shell commands are unavailable. Finish with a concise summary."
            + feedback,
            restore_mutations=retained is False,
            assignment=assignment,
        )
        payload = await asyncio.to_thread(self.runner.collect, task.id)
        ref = await asyncio.to_thread(self._persist, admission, task.id, payload, "candidate")
        from rhetorica_backend.domain.coding.candidate_manifest import (
            CodingCandidateManifestFactory,
        )

        candidate = CodingCandidateManifestFactory().create(
            run=r, base=base, payload=payload, artifact_id=ref
        )
        return DeveloperExecution(
            candidate=candidate,
            checkpoint_ref=ref,
            artifact_ids=(*preparation_artifacts, ref),
            summary=str(output or ""),
        )

    async def qa(self, *, admission, task, candidate, operation_id, deadline, assignment=""):
        payload = await asyncio.to_thread(
            self._artifact, admission, candidate.artifact_id, self._producer_task_id(candidate)
        )
        # Independent QA always stages the exact immutable candidate in its own session.
        await asyncio.to_thread(self.runner.close, task.id)
        await asyncio.to_thread(self.runner.stage, task.id, payload)
        preparation_artifacts = await self._prepare_environment(admission, task, deadline)
        result = await self.agents.run(
            admission,
            task,
            operation_id,
            deadline,
            "Independently inspect the candidate and verify the user's task. "
            "Inspect the supplied changed paths directly. Run relevant tests. "
            "For dbt use parse, targeted compile (no_introspect=true, no_populate_cache=true), "
            "then show (no_introspect=false) for the changed model; "
            "production relation tests are baseline evidence. Do not edit source files. "
            "Finish with status passed, failed, or blocked and a short reason: through submit_qa_verdict "
            "when that tool is available, otherwise as the JSON schema object. "
            "Use blocked if required validation is unavailable. Materialization (dbt run/build/seed/snapshot) "
            "is never available and the server records it as not_run: do not block only because of it, "
            "and never claim materialization was tested.",
            candidate=candidate,
            assignment=assignment,
        )
        after = await asyncio.to_thread(self.runner.collect, task.id)
        unchanged = tree_from_candidate_archive(payload) == tree_from_candidate_archive(after)
        checks = [
            self._evidence(
                admission,
                task,
                candidate,
                "candidate_integrity",
                "passed" if unchanged else "failed",
                True,
            )
        ]
        if admission.run.data_warehouse:
            checks.append(
                self._evidence(
                    admission,
                    task,
                    candidate,
                    "materialization",
                    "not_run",
                    False,
                    "Read-only validation; dbt run/build/seed/snapshot were not executed",
                )
            )
        try:
            verdict_input = QaResult.model_validate_json(str(result))
            status = (
                verdict_input.status
                if verdict_input.status in {"passed", "failed", "blocked"}
                else "blocked"
            )
            report = verdict_input.reason
        except (ValueError, TypeError):
            status, report = "blocked", "QA did not return a valid structured verdict"
        if not unchanged:
            status, report = "failed", "QA modified the immutable candidate"
        reason = report[:512]
        verdict = QaVerdict(
            id=str(uuid4()),
            qa_agent_id=task.agent_profile_id,
            task_id=task.id,
            candidate_digest=candidate.digest,
            status=status,
            reason=reason,
            created_at=datetime.now(UTC),
        )
        return QaExecution(
            verdict=verdict,
            evidence=tuple(checks),
            checkpoint_ref=candidate.artifact_id,
            artifact_ids=preparation_artifacts,
            summary=report,
        )

    def _evidence(self, admission, task, candidate, name, status, mandatory, reason=None):
        r = admission.run
        return ServerValidationEvidence(
            id=str(uuid4()),
            workspace_id=r.workspace_id,
            owner_user_id=r.owner_user_id,
            run_id=r.id,
            task_id=task.id,
            candidate_digest=candidate.digest,
            name=name,
            source="system",
            status=status,
            mandatory=mandatory,
            duration_ms=0,
            reason=reason,
            created_at=datetime.now(UTC),
        )

    async def publish(self, *, admission, candidate, report):
        try:
            return await asyncio.to_thread(
                self.publisher.publish,
                run_id=admission.run.id,
                candidate=candidate,
                report=report,
                principal=admission.principal,
            )
        finally:
            for task in self._tasks(admission):
                if task.role in {"developer", "qa"}:
                    await asyncio.to_thread(self.runner.close, task.id)

    async def coordinate(self, *, admission, task, operation_id, deadline, invoke_member):
        from rhetorica_backend.domain.coding.lifecycle import CoordinatorExecution

        decision = await self.coordinator.run(
            admission,
            task,
            operation_id,
            deadline,
            invoke_member=invoke_member,
        )
        return CoordinatorExecution(decision=decision)

    async def cancel(self, *, admission):
        artifacts = []
        candidate_recorded = admission.run.candidate is not None
        for task in self._tasks(admission):
            if task.role not in {"developer", "qa"}:
                continue
            try:
                payload = await asyncio.to_thread(self.runner.collect, task.id)
                ref = await asyncio.to_thread(self._persist, admission, task.id, payload, "partial")
                artifacts.append(ref)
                if task.role == "developer" and not candidate_recorded:
                    coordinator = next(t for t in self._tasks(admission) if t.role == "coordinator")
                    base = await asyncio.to_thread(
                        self._artifact, admission, coordinator.checkpoint_ref, coordinator.id
                    )
                    from rhetorica_backend.domain.coding.candidate_manifest import (
                        CodingCandidateManifestFactory,
                    )

                    candidate = CodingCandidateManifestFactory().create(
                        run=admission.run, base=base, payload=payload, artifact_id=ref
                    )
                    await asyncio.to_thread(self.service.runs.record_candidate, candidate)
                    candidate_recorded = True
            except Exception as exc:
                logging.getLogger(__name__).info(
                    "Partial coding candidate unavailable (%s)", type(exc).__name__
                )
            await asyncio.to_thread(self.runner.close, task.id)
        return tuple(artifacts)
