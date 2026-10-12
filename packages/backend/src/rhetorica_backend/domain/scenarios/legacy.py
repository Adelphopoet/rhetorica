"""Lossless prompt snapshot and its editor representation."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.scenario_workflows import (
    InputBinding,
    InputNode,
    LlmNode,
    ResultNode,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
    ValueSpec,
    ValueType,
)


def legacy_snapshot(definition: Any) -> dict[str, Any]:
    policy = definition.tool_policy
    bindings = definition.default_connector_bindings
    return {
        "prompt_template": definition.prompt_template,
        "input_schema": definition.input_schema,
        "tool_policy": policy.model_dump(mode="json") if hasattr(policy, "model_dump") else policy,
        "default_connector_bindings": bindings.model_dump(mode="json")
        if hasattr(bindings, "model_dump")
        else bindings,
    }


def legacy_workflow(snapshot: dict[str, Any]) -> ScenarioWorkflowDefinition:
    schema = snapshot.get("input_schema") or {}
    types: dict[str, ValueType] = {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
    }
    parameters = {
        name: ValueSpec(
            type=types.get(spec.get("type"), "json"),
            title=spec.get("title", name),
            required=name in schema.get("required", []),
            default=spec.get("default"),
            json_schema=spec,
        )
        for name, spec in schema.get("properties", {}).items()
    }
    policy = snapshot.get("tool_policy") or {}
    bindings = snapshot.get("default_connector_bindings") or {}
    permitted = {"run_sql", "list_database_connectors", "list_files", "run_python"}
    tools = (
        set(policy.get("allowed_tools") or {"run_sql", "list_database_connectors", "list_files"})
        & permitted
    )
    if not policy.get("allow_run_sql", True) or bindings.get("run_sql") is False:
        tools.discard("run_sql")
    return ScenarioWorkflowDefinition(
        allowed_tools=sorted(tools),
        parameters=parameters,
        nodes=[
            InputNode(id="start", title="Параметры"),
            LlmNode(
                id="agent",
                title="Сохранённый промпт",
                instructions=snapshot["prompt_template"],
                allowed_tools=sorted(tools),
                inputs={
                    key: InputBinding(
                        source="input",
                        path=[key],
                        type=spec.type,
                        required=spec.required,
                        default=spec.default,
                    )
                    for key, spec in parameters.items()
                },
            ),
            ResultNode(
                id="result",
                title="Результат",
                inputs={
                    "answer_markdown": InputBinding(
                        source="step", node_id="agent", path=["result"], type="string"
                    )
                },
            ),
        ],
        edges=[
            ScenarioEdge(source="start", target="agent"),
            ScenarioEdge(source="agent", target="result"),
        ],
    )
