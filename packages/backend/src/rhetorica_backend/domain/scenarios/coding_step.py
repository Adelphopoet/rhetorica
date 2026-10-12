"""A scenario invokes the same coding use case with its admitted web identity."""

import asyncio

from rhetorica_backend.contracts.coding import CreateCodingRunRequest
from rhetorica_backend.domain.scenarios.errors import WorkflowError


def request_for_node(node, task: str):
    return CreateCodingRunRequest(
        task=task,
        dbt_project_subdir=node.dbt_project_subdir,
        root_agent_id=node.root_agent_id,
        developer_agent_id=node.developer_agent_id,
        qa_agent_id=node.qa_agent_id,
        gitlab_connector_id=node.gitlab_connector_id,
        project_id=node.project_id,
        target_branch=node.target_branch,
        environment_profile_id=node.environment_profile_id,
        database_connector_id=node.database_connector_id,
    )


class ScenarioCodingStepRunner:
    def __init__(self, coding, repository):
        self.coding = coding
        self.repository = repository

    async def run(self, node, inputs, context):
        parent = await asyncio.to_thread(self.repository.load_run, context.run_id)
        auth = parent.agent_snapshots.get("_coding_auth", {})
        if auth != {"auth_kind": "session", "entry_source": "web_scenario"}:
            raise WorkflowError("channel_not_allowed", status_code=403)
        task = inputs.get("task")
        if not isinstance(task, str) or not task.strip():
            raise WorkflowError("coding_task_input_required")
        admitted = await asyncio.to_thread(
            self.coding.create,
            request_for_node(node, task),
            context.user,
            auth_kind=auth["auth_kind"],
            source="web_scenario",
            scenario_id=parent.scenario_id,
            idempotency_key=f"scenario:{context.run_id}:{context.node_id}",
        )
        try:
            await asyncio.to_thread(
                self.repository.update_diagnostics,
                context.run_id,
                context.node_id,
                context.attempt,
                {"coding_run_id": admitted.run.id},
            )
            while True:
                view = await asyncio.to_thread(self.coding.view, admitted.run.id, context.user)
                if view.run.status in {"succeeded", "failed", "cancelled", "rejected"}:
                    return {
                        "run_id": view.run.id,
                        "status": view.run.status,
                        "report": {
                            "id": view.report.id,
                            "checks": [c.model_dump(mode="json") for c in view.report.checks],
                        }
                        if view.report
                        else None,
                        "candidate": {
                            "digest": view.run.candidate.digest,
                            "changed_paths": view.run.candidate.changed_paths,
                        }
                        if view.run.candidate
                        else None,
                        "mr_url": view.receipt.merge_request_url if view.receipt else None,
                    }
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.to_thread(self.coding.cancel, admitted.run.id, context.user)
            )
            raise
