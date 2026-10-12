"""Transport-neutral owner-scoped coding-run progress reader."""

from __future__ import annotations

from rhetorica_backend.contracts.coding import CodingRunProgress
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository


class CodingRunProgressReader:
    def __init__(self, runs: CodingRunRepository) -> None:
        self._runs = runs

    def read(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        after: int = 0,
        limit: int = 200,
    ) -> CodingRunProgress:
        run = self._runs.get(
            run_id=run_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        )
        events = self._runs.events(
            run_id=run_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            after=after,
            limit=limit,
        )
        tasks = self._runs.tasks(
            run_id=run_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
        )
        artifact_ids = [task.instruction_artifact_id for task in tasks]
        artifact_ids.extend(ref for task in tasks for ref in task.result_artifact_ids)
        if run.candidate is not None:
            artifact_ids.append(run.candidate.artifact_id)
        if run.partial_result is not None:
            artifact_ids.extend(run.partial_result.artifact_ids)
        return CodingRunProgress(
            run_id=run.id,
            workspace_id=run.workspace_id,
            cursor=events[-1].sequence if events else after,
            phase=run.phase,
            status=run.status,
            reason_code=run.reason_code,
            events=events,
            tasks=tasks,
            budgets=self._runs.budget_ledgers(
                run_id=run_id,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
            ),
            artifact_ids=list(dict.fromkeys(artifact_ids)),
        )
