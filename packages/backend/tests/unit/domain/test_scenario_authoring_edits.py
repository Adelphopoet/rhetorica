import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.scenario_workflows import (
    InputNode,
    LlmNode,
    ResultNode,
    ScenarioAiProposalRequest,
    ScenarioDraft,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
    ValueSpec,
)
from rhetorica_backend.domain.scenarios.authoring_assistant import ScenarioAuthoringAssistant
from rhetorica_backend.domain.scenarios.authoring_edits import (
    WorkflowEditProposal,
    apply_workflow_edits,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.graph import validate_workflow

from agents import ModelBehaviorError


def graph():
    return ScenarioWorkflowDefinition(
        nodes=[
            InputNode(id="input", title="Input"),
            LlmNode(id="agent", title="Agent", agent_id="agent", instructions="Explain"),
            ResultNode(id="result", title="Result"),
        ],
        edges=[
            ScenarioEdge(source="input", target="agent"),
            ScenarioEdge(source="agent", target="result"),
        ],
    )


def proposal(operations, complete=True):
    return WorkflowEditProposal(summary="Изменение", operations=operations, complete=complete)


def test_instruction_edit_preserves_every_other_node_field_and_edge():
    original = graph()
    changed = apply_workflow_edits(
        original,
        proposal([
            {
                "op": "replace",
                "path": "/nodes/agent/instructions",
                "value": "Explain and add a joke",
            }
        ]),
    )
    expected = original.model_copy(deep=True)
    expected.nodes[1].instructions = "Explain and add a joke"
    assert changed == expected
    assert original.nodes[1].instructions == "Explain"


def test_edits_support_escaped_parameter_names_and_array_insertion():
    original = graph()
    changed = apply_workflow_edits(
        original,
        proposal([
            {"op": "add", "path": "/parameters/a~1b~0c", "value": {"type": "number"}},
            {"op": "add", "path": "/allowed_tools/-", "value": "list_files"},
            {"op": "add", "path": "/allowed_tools/0", "value": "run_python"},
            {"op": "remove", "path": "/allowed_tools/1"},
        ]),
    )
    assert changed.parameters == {"a/b~c": ValueSpec(type="number")}
    assert changed.allowed_tools == ["run_python"]


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "replace", "path": "/nodes/agent/id", "value": "another"},
        {"op": "replace", "path": "/nodes", "value": {}},
        {"op": "replace", "path": "/schema_version", "value": 2},
        {"op": "replace", "path": "/nodes/missing/title", "value": "No"},
        {"op": "add", "path": "/parameters/invalid~2escape", "value": {}},
        {"op": "replace", "path": "/nodes/agent/instructions"},
        {"op": "remove", "path": "/edges/-1"},
        {"op": "remove", "path": "/edges/2"},
        {
            "op": "add",
            "path": "/nodes/new",
            "value": {"id": "different", "type": "input", "title": "No"},
        },
    ],
)
def test_invalid_edit_is_atomic(edit):
    original = graph()
    before = original.model_dump()
    with pytest.raises((WorkflowError, ValidationError)):
        apply_workflow_edits(
            original,
            proposal([
                {"op": "replace", "path": "/nodes/agent/title", "value": "Changed"},
                edit,
            ]),
        )
    assert original.model_dump() == before


def assistant(responses):
    llm = SimpleNamespace(run=AsyncMock(side_effect=responses))
    resources = SimpleNamespace(
        snapshots=Mock(return_value={"proposal": {}}), capabilities=Mock(return_value={})
    )
    return ScenarioAuthoringAssistant(llm, resources, lambda g, _u: validate_workflow(g))


def response(operations, complete=True):
    return {"result": proposal(operations, complete).model_dump(mode="json")}


REQUEST = ScenarioAiProposalRequest(
    agent_id="agent", instruction="Собери пайплайн", expected_revision=0
)


def test_builds_from_empty_in_parts_and_validates_only_the_finished_graph():
    app = assistant([
        response(
            [{"op": "add", "path": "/nodes/input", "value": {"type": "input", "title": "Inputs"}}],
            False,
        ),
        response(
            [
                {
                    "op": "add",
                    "path": "/nodes/result",
                    "value": {"type": "result", "title": "Result"},
                }
            ],
            False,
        ),
        response([
            {"op": "add", "path": "/edges/-", "value": {"source": "input", "target": "result"}}
        ]),
    ])
    draft = ScenarioDraft(scenario_id="s", revision=0, workflow=ScenarioWorkflowDefinition())
    progress = AsyncMock()
    result = asyncio.run(app.propose(draft, SimpleNamespace(), REQUEST, progress))
    assert result.validation.valid
    assert len(result.workflow.nodes) == 2
    assert draft.workflow.nodes == []
    calls = app.llm.run.call_args_list
    assert set(calls[1].args[1]["current_workflow"]["nodes"]) == {"input"}
    assert set(calls[2].args[1]["current_workflow"]["nodes"]) == {"input", "result"}
    assert len(calls[2].args[1]["completed_changes"]) == 2
    assert [c.args[0].phase for c in progress.call_args_list] == [
        "generating",
        "generating",
        "generating",
        "validating",
    ]


def test_graph_repair_receives_errors_and_keeps_previous_batches():
    app = assistant([
        response([{"op": "replace", "path": "/nodes/agent/title", "value": "Saved batch"}], False),
        response([
            {"op": "add", "path": "/edges/-", "value": {"source": "result", "target": "input"}}
        ]),
        response([{"op": "remove", "path": "/edges/2"}]),
    ])
    result = asyncio.run(
        app.propose(
            ScenarioDraft(scenario_id="s", revision=0, workflow=graph()), SimpleNamespace(), REQUEST
        )
    )
    assert result.validation.valid
    assert result.workflow.nodes[1].title == "Saved batch"
    repair = app.llm.run.call_args_list[2].args[1]
    assert repair["validation_errors"][0]["code"] == "graph_cycle"
    assert repair["previous_attempt"] is None
    assert repair["current_workflow"]["edges"][-1] == {"source": "result", "target": "input"}


def test_truncated_response_can_resume_with_smaller_parts():
    app = assistant([ModelBehaviorError("private truncated content"), response([])])
    result = asyncio.run(
        app.propose(
            ScenarioDraft(scenario_id="s", revision=0, workflow=graph()), SimpleNamespace(), REQUEST
        )
    )
    assert result.validation.valid
    assert "private" not in str(app.llm.run.call_args_list[1].args[1])
    assert (
        app.llm.run.call_args_list[1].args[1]["validation_errors"][0]["code"]
        == "invalid_response_format"
    )


def test_empty_incomplete_batches_fail_instead_of_looping():
    app = assistant([response([], False)] * 3)
    with pytest.raises(WorkflowError, match="scenario_ai_invalid_output"):
        asyncio.run(
            app.propose(
                ScenarioDraft(scenario_id="s", revision=0, workflow=graph()),
                SimpleNamespace(),
                REQUEST,
            )
        )
    assert app.llm.run.call_count == 3


def test_connection_retry_preserves_completed_batches():
    import httpx2
    from openai import APIConnectionError

    first = response(
        [{"op": "replace", "path": "/nodes/agent/title", "value": "Saved batch"}], False
    )
    app = assistant([
        first,
        APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid")),
        response([]),
    ])
    progress = AsyncMock()
    result = asyncio.run(
        app.propose(
            ScenarioDraft(scenario_id="s", revision=0, workflow=graph()),
            SimpleNamespace(),
            REQUEST,
            progress,
        )
    )
    assert result.workflow.nodes[1].title == "Saved batch"
    calls = app.llm.run.call_args_list
    assert calls[1].args[1] == calls[2].args[1]
    assert any("Соединение" in call.args[0].message for call in progress.call_args_list)


def test_iteration_budget_does_not_return_or_save_an_unfinished_graph(monkeypatch):
    from rhetorica_backend.domain.scenarios import authoring_assistant

    monkeypatch.setattr(authoring_assistant, "MAX_AUTHORING_BATCHES", 2)
    app = assistant([
        response([{"op": "replace", "path": "/nodes/agent/title", "value": title}], False)
        for title in ["Part 1", "Part 2"]
    ])
    draft = ScenarioDraft(scenario_id="s", revision=0, workflow=graph())
    with pytest.raises(WorkflowError, match="scenario_ai_iteration_limit"):
        asyncio.run(app.propose(draft, SimpleNamespace(), REQUEST))
    assert draft.workflow == graph()
    assert app.llm.run.call_count == 2
