"""I/O boundary for workflow execution; Temporal history contains compact references."""

from __future__ import annotations

import asyncio
import contextlib
from time import monotonic
from typing import Any

from openai import APIStatusError
from rhetorica_backend.contracts.scenario_workflows import ScenarioWorkflowDefinition
from rhetorica_backend.domain.files.ports import SandboxGatewayError
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.graph import evaluate_condition
from rhetorica_backend.domain.scenarios.legacy import legacy_workflow
from rhetorica_backend.domain.scenarios.llm_step import ScenarioLlmStepRunner
from rhetorica_backend.domain.scenarios.llm_tools import ScenarioLlmTools
from rhetorica_backend.domain.scenarios.python_step import ScenarioPythonStepRunner
from rhetorica_backend.domain.scenarios.resources import ScenarioResourcePolicy
from rhetorica_backend.domain.scenarios.sql_step import ScenarioSqlStepRunner
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext
from rhetorica_backend.domain.scenarios.values import (
    resolve_bindings,
    validate_schema,
    validate_value,
)
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agents import MaxTurnsExceeded, ModelBehaviorError


class ScenarioActivities:
    def __init__(
        self,
        repository: ScenarioActivityRepository,
        data: ScenarioDataStore,
        resources: ScenarioResourcePolicy,
        sql: ScenarioSqlStepRunner,
        python: ScenarioPythonStepRunner,
        llm: ScenarioLlmStepRunner,
        tools: ScenarioLlmTools,
    ):
        self.repository = repository
        self.data = data
        self.resources = resources
        self.sql = sql
        self.python = python
        self.llm = llm
        self.tools = tools

    @activity.defn(name="scenario_bootstrap")
    async def bootstrap(self, run_id: str) -> dict[str, Any]:
        run = await asyncio.to_thread(self.repository.load_run, run_id)
        graph = (
            legacy_workflow(run.definition)
            if run.kind == "legacy"
            else ScenarioWorkflowDefinition.model_validate(run.definition)
        )
        return {
            "nodes": [{"id": node.id, "type": node.type} for node in graph.nodes],
            "edges": [edge.model_dump() for edge in graph.edges],
            "test_node_id": run.test_config.get("node_id"),
        }

    @activity.defn(name="scenario_step")
    async def step(self, command: dict[str, Any]) -> dict[str, Any]:
        async def heartbeat() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(5)

        pulse = asyncio.create_task(heartbeat())
        try:
            return await self._step(command, activity.info().attempt)
        except Exception as exc:
            non_retryable = isinstance(
                exc,
                WorkflowError
                | ValueError
                | PermissionError
                | LookupError
                | ModelBehaviorError
                | MaxTurnsExceeded,
            )
            if isinstance(exc, SandboxGatewayError):
                non_retryable = not exc.retry_recommended
            if isinstance(exc, APIStatusError):
                non_retryable = exc.status_code < 500 and exc.status_code not in {408, 429}
            await asyncio.to_thread(
                self.repository.fail_step,
                command["run_id"],
                command["node_id"],
                activity.info().attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            if non_retryable:
                raise ApplicationError(
                    str(exc), type=str(getattr(exc, "code", type(exc).__name__)), non_retryable=True
                ) from exc
            raise
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse

    async def _step(self, command: dict[str, Any], attempt: int) -> dict[str, Any]:
        run_id, node_id = command["run_id"], command["node_id"]
        run = await asyncio.to_thread(self.repository.load_run, run_id)
        graph = (
            legacy_workflow(run.definition)
            if run.kind == "legacy"
            else ScenarioWorkflowDefinition.model_validate(run.definition)
        )
        node = next(item for item in graph.nodes if item.id == node_id)
        step = await asyncio.to_thread(self.repository.load_step, run_id, node_id)
        if command.get("skip"):
            await asyncio.to_thread(
                self.repository.finish_step, run_id, node_id, step.attempt, status="skipped"
            )
            return {"status": "skipped"}
        # Rights are checked even when recovering a persisted result before another operation.
        user = await asyncio.to_thread(self.resources.user, run.owner_user_id, run.workspace_id)
        if step.status == "succeeded":
            output = await asyncio.to_thread(self.data.get, step.output_ref or "", run_id=run_id)
            return {
                "status": "succeeded",
                "output_ref": step.output_ref,
                "branch": output.get("branch"),
            }
        if command.get("approve"):
            if step.decision is not True:
                raise WorkflowError("approval_not_recorded")
            output = await asyncio.to_thread(self.data.get, step.input_ref or "", run_id=run_id)
            return await self._commit(run_id, node_id, step.attempt, output, 0)
        outputs: dict[str, dict[str, Any]] = {}
        for predecessor in await asyncio.to_thread(self.repository.list_steps, run_id):
            if predecessor.status == "succeeded" and predecessor.output_ref:
                outputs[predecessor.node_id] = await asyncio.to_thread(
                    self.data.get, predecessor.output_ref, run_id=run_id
                )
        if run.test_config.get("node_id") == node_id:
            inputs = run.test_config["inputs"]
        else:
            inputs = resolve_bindings(node.inputs, run.inputs, outputs)
        if node.type == "input" and not run.test_config.get("node_id"):
            inputs = run.inputs
        ref = await asyncio.to_thread(
            self.data.put,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            role="inputs",
            value=inputs,
        )
        step = await asyncio.to_thread(self.repository.begin_step, run_id, node_id, attempt, ref)
        recovered = await asyncio.to_thread(self.data.committed, run_id, node_id)
        if recovered is not None:
            return await self._commit(run_id, node_id, attempt, recovered, 0)
        context = ScenarioStepContext(
            run_id,
            node_id,
            step.operation_id,
            attempt,
            user,
            run.conversation_id or "",
            run.agent_snapshots.get(node_id),
        )
        started = monotonic()
        if node.type == "sql":
            output = await self.sql.run(node, inputs, context)
        elif node.type == "python":
            output = await self.python.run(node, inputs, context)
        elif node.type == "llm":
            if run.kind == "legacy":
                # The legacy template remains literal except for its declared placeholders.
                from rhetorica_backend.domain.scenarios.service import ScenarioCatalogService

                node = node.model_copy(
                    update={
                        "instructions": ScenarioCatalogService._render_prompt(
                            node.instructions, run.inputs
                        )
                    }
                )
            output = await self.llm.run(
                node, inputs, context, self.tools.build(node, context, graph.allowed_tools)
            )
        elif node.type == "condition":
            output = {"branch": evaluate_condition(node, inputs)}
        elif node.type == "approval":
            await asyncio.to_thread(
                self.repository.finish_step,
                run_id,
                node_id,
                attempt,
                status="waiting",
                diagnostics={"message": node.message},
            )
            return {"status": "waiting"}
        else:
            output = inputs
        for name, spec in node.outputs.items():
            validate_value(output.get(name), spec)
        if (
            node.type == "result" or run.test_config.get("node_id") == node_id
        ) and run.test_config.get("expected_schema") is not None:
            validate_schema(output, run.test_config["expected_schema"])
        return await self._commit(
            run_id, node_id, attempt, output, int((monotonic() - started) * 1000)
        )

    async def _commit(
        self, run_id: str, node_id: str, attempt: int, output: dict[str, Any], duration: int
    ) -> dict[str, Any]:
        ref = await asyncio.to_thread(
            self.repository.commit_result, run_id, node_id, attempt, self.data, output, duration
        )
        return {"status": "succeeded", "output_ref": ref, "branch": output.get("branch")}

    @activity.defn(name="scenario_finish")
    async def finish(self, command: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self.repository.finish_run,
            command["run_id"],
            command["status"],
            result_ref=command.get("result_ref"),
            error=command.get("error"),
        )
