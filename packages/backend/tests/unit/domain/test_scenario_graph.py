from __future__ import annotations

import pytest
from rhetorica_backend.contracts.scenario_workflows import (
    ConditionNode,
    ConditionRule,
    InputBinding,
    InputNode,
    JoinNode,
    ParallelNode,
    ResultNode,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
    ValueSpec,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.graph import evaluate_condition, validate_workflow
from rhetorica_backend.domain.scenarios.values import (
    check_schema,
    resolve_bindings,
    validate_parameters,
    validate_schema,
)


def diamond() -> ScenarioWorkflowDefinition:
    return ScenarioWorkflowDefinition(
        parameters={"amount": ValueSpec(type="number")},
        nodes=[
            InputNode(id="start", title="Input"),
            ConditionNode(
                id="check",
                title="Check",
                inputs={"value": InputBinding(source="input", type="number", path=["amount"])},
                rules=[ConditionRule(operator="gt", value=10)],
            ),
            ParallelNode(
                id="yes",
                title="Yes",
                outputs={"value": ValueSpec(type="string")},
                inputs={"value": InputBinding(value="yes", type="string")},
            ),
            ParallelNode(
                id="no",
                title="No",
                outputs={"value": ValueSpec(type="string")},
                inputs={"value": InputBinding(value="no", type="string")},
            ),
            JoinNode(
                id="merge",
                title="Merge",
                inputs={
                    "yes": InputBinding(
                        source="step", node_id="yes", path=["value"], required=False
                    ),
                    "no": InputBinding(source="step", node_id="no", path=["value"], required=False),
                },
                outputs={"yes": ValueSpec(required=False), "no": ValueSpec(required=False)},
            ),
            ResultNode(
                id="end",
                title="Result",
                inputs={
                    "answer": InputBinding(
                        source="step", node_id="merge", path=["yes"], required=False
                    )
                },
            ),
        ],
        edges=[
            ScenarioEdge(source="start", target="check"),
            ScenarioEdge(source="check", target="yes", branch="true"),
            ScenarioEdge(source="check", target="no", branch="false"),
            ScenarioEdge(source="yes", target="merge"),
            ScenarioEdge(source="no", target="merge"),
            ScenarioEdge(source="merge", target="end"),
        ],
    )


def test_diamond_accepts_explicit_optional_merge_inputs() -> None:
    result = validate_workflow(diamond())
    assert result.valid, result.issues


def test_required_reference_to_skipped_branch_rejected() -> None:
    definition = diamond()
    definition.nodes[-1].inputs["answer"] = InputBinding(
        source="step", node_id="yes", path=["value"], type="string"
    )
    assert "possibly_skipped_input" in {item.code for item in validate_workflow(definition).issues}


def test_cycle_rejected_before_ancestry_evaluation() -> None:
    definition = diamond()
    definition.edges.append(ScenarioEdge(source="merge", target="check"))
    assert "graph_cycle" in {item.code for item in validate_workflow(definition).issues}


def test_unknown_port_and_wrong_type_report_node_field() -> None:
    definition = diamond()
    definition.nodes[1].inputs["value"].type = "string"
    issue = next(
        item for item in validate_workflow(definition).issues if item.code == "input_type_mismatch"
    )
    assert (issue.node_id, issue.field) == ("check", "value")
    definition.nodes[1].inputs["value"].path = ["missing"]
    assert "unknown_input_reference" in {item.code for item in validate_workflow(definition).issues}


def test_bindings_preserve_file_reference_between_steps() -> None:
    file_ref = {"id": "owned-file", "filename": "sales.csv"}
    result = resolve_bindings(
        {"sales": InputBinding(source="step", node_id="sql", path=["file"], type="file")},
        {},
        {"sql": {"file": file_ref}},
    )
    assert result == {"sales": file_ref}


def test_missing_required_input_fails_and_optional_uses_default() -> None:
    binding = InputBinding(source="step", node_id="skipped", path=["result"])
    with pytest.raises(WorkflowError, match="skipped"):
        resolve_bindings({"value": binding}, {}, {})
    binding.required = False
    binding.default = "fallback"
    assert resolve_bindings({"value": binding}, {}, {}) == {"value": "fallback"}


def test_parameters_validate_without_coercing_boolean_to_number() -> None:
    with pytest.raises(WorkflowError, match="number"):
        validate_parameters({"count": True}, {"count": ValueSpec(type="number")})
    assert validate_parameters({}, {"count": ValueSpec(type="number", default=2)}) == {"count": 2}


def test_schema_cannot_resolve_network_references() -> None:
    with pytest.raises(WorkflowError, match="external_schema"):
        check_schema({"$ref": "https://example.com/schema.json"})
    with pytest.raises(WorkflowError, match="is not of type"):
        validate_schema(
            {"count": "wrong"}, {"type": "object", "properties": {"count": {"type": "number"}}}
        )


def test_condition_uses_explicit_operator() -> None:
    node = diamond().nodes[1]
    assert isinstance(node, ConditionNode)
    assert evaluate_condition(node, {"value": 11})
    assert not evaluate_condition(node, {"value": 2})
    with pytest.raises(WorkflowError, match="value"):
        evaluate_condition(node, {"value": "eleven"})


def test_isolated_sql_step_can_run_with_unfinished_agent_and_explicit_inputs():
    from rhetorica_backend.domain.scenarios.graph import isolated_step_definition

    graph = ScenarioWorkflowDefinition.model_validate({
        "nodes": [
            {"id": "start", "title": "Start", "type": "input"},
            {
                "id": "sql",
                "title": "SQL",
                "type": "sql",
                "connector_name": "db",
                "sql": "SELECT :value",
                "inputs": {
                    "value": {
                        "source": "step",
                        "node_id": "missing",
                        "path": ["value"],
                        "type": "number",
                    }
                },
            },
            {"id": "agent", "title": "Unfinished", "type": "llm", "agent_id": ""},
            {"id": "end", "title": "Result", "type": "result"},
        ],
        "edges": [
            {"source": "start", "target": "sql"},
            {"source": "sql", "target": "agent"},
            {"source": "agent", "target": "end"},
        ],
    })
    assert not validate_workflow(graph).valid
    isolated = isolated_step_definition(graph, "sql", {"value": 42})
    assert validate_workflow(isolated).valid
    assert isolated.nodes[1].inputs["value"].value == 42
    with pytest.raises(WorkflowError, match="type_mismatch"):
        isolated_step_definition(graph, "sql", {"value": "bad"})
