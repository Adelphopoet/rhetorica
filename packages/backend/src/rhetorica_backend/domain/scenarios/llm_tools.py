"""Server-owned analytics tool registry; connector annotations never grant access."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.scenario_workflows import LlmNode, PythonNode, SqlNode
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.python_step import ScenarioPythonStepRunner
from rhetorica_backend.domain.scenarios.resources import ANALYTICAL_TOOLS, ScenarioResourcePolicy
from rhetorica_backend.domain.scenarios.sql_step import ScenarioSqlStepRunner
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository

from agents import FunctionTool

TOOL_SCHEMAS = {
    "run_sql": {
        "type": "object",
        "properties": {
            "connector_name": {"type": "string"},
            "sql": {"type": "string"},
            "parameters": {"type": "object"},
        },
        "required": ["connector_name", "sql"],
    },
    "run_python": {
        "type": "object",
        "properties": {"code": {"type": "string"}, "inputs": {"type": "object"}},
        "required": ["code", "inputs"],
    },
    "list_files": {"type": "object", "properties": {}},
    "list_database_connectors": {"type": "object", "properties": {}},
}


class ScenarioLlmTools:
    def __init__(
        self,
        sql: ScenarioSqlStepRunner,
        python: ScenarioPythonStepRunner,
        resources: ScenarioResourcePolicy,
        repository: ScenarioActivityRepository,
        data: ScenarioDataStore,
    ):
        self.sql = sql
        self.python = python
        self.resources = resources
        self.repository = repository
        self.data = data

    def build(
        self, node: LlmNode, context: ScenarioStepContext, workflow_tools: list[str]
    ) -> list[FunctionTool]:
        allowed = set(node.allowed_tools) & set(workflow_tools) & set(ANALYTICAL_TOOLS)
        traces: list[dict[str, Any]] = []
        lock = asyncio.Lock()

        def make_tool(name: str) -> FunctionTool:
            async def invoke(_sdk_context: Any, args: str) -> str:
                # Serialize tool calls within a step for stable traces and sandbox isolation.
                async with lock:
                    user = await asyncio.to_thread(
                        self.resources.user, context.user.id, context.user.workspace_id
                    )
                    saved = (context.agent_snapshot or {}).get("agent")
                    live = (
                        await asyncio.to_thread(self.resources.agent, saved["id"], user)
                        if saved
                        else None
                    )
                    values = json.loads(args)
                    digest = hashlib.sha256(
                        (name + json.dumps(values, sort_keys=True)).encode()
                    ).hexdigest()
                    operation = str(uuid5(NAMESPACE_URL, f"{context.operation_id}/tool/{digest}"))
                    child = replace(context, operation_id=operation, user=user)
                    if name == "run_sql":
                        output = await self.sql.run(
                            SqlNode(
                                id=node.id,
                                title="SQL",
                                connector_name=values["connector_name"],
                                sql=values["sql"],
                            ),
                            values.get("parameters", {}),
                            child,
                        )
                    elif name == "run_python":
                        output = await self.python.run(
                            PythonNode(id=node.id, title="Python", code=values["code"]),
                            values["inputs"],
                            child,
                        )
                    elif name == "list_database_connectors":
                        names = (
                            set(saved["allowed_database_connector_names"])
                            & set(live.allowed_database_connector_names)
                            if saved and live
                            else set()
                        )
                        if not saved:
                            available = await asyncio.to_thread(self.resources.capabilities, user)
                            bound = (context.agent_snapshot or {}).get(
                                "legacy_database_connector_name"
                            )
                            names = {
                                item["name"]
                                for item in available["database_connectors"]
                                if not bound or item["name"] == bound
                            }
                        output = {"connectors": sorted(names)}
                    elif name == "list_files":
                        output = {
                            "files": await asyncio.to_thread(
                                self.repository.list_artifacts, context.run_id
                            )
                        }
                    else:
                        raise WorkflowError("tool_not_allowed")
                    ref = await asyncio.to_thread(
                        self.data.put,
                        run_id=context.run_id,
                        node_id=context.node_id,
                        attempt=context.attempt,
                        role=f"tool-{digest}",
                        value=output,
                    )
                    traces.append({
                        "tool": name,
                        "arguments": values,
                        "output_ref": ref,
                        "operation_id": operation,
                    })
                    await asyncio.to_thread(
                        self.repository.update_diagnostics,
                        context.run_id,
                        context.node_id,
                        context.attempt,
                        {"tool_calls": traces},
                    )
                    return json.dumps(output, ensure_ascii=False)

            return FunctionTool(
                name=name,
                description=ANALYTICAL_TOOLS[name],
                params_json_schema={**TOOL_SCHEMAS[name], "additionalProperties": False},
                on_invoke_tool=invoke,
                strict_json_schema=False,
            )

        return [make_tool(name) for name in sorted(allowed)]
