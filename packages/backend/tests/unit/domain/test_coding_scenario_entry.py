import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rhetorica_backend.contracts.scenario_workflows import (
    CodingTaskNode,
    InputBinding,
    InputNode,
    ResultNode,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
)
from rhetorica_backend.domain.scenarios.coding_step import ScenarioCodingStepRunner
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.graph import validate_workflow


def graph(version=2):
    return ScenarioWorkflowDefinition(
        schema_version=version,
        nodes=[
            InputNode(id="input", title="input"),
            CodingTaskNode(
                id="coding",
                title="coding",
                root_agent_id="root",
                developer_agent_id="dev",
                qa_agent_id="qa",
                gitlab_connector_id="gitlab",
                project_id="group/project",
                target_branch="main",
                environment_profile_id="env",
                inputs={
                    "task": InputBinding(
                        source="constant", type="string", value="Implement net_revenue"
                    )
                },
            ),
            ResultNode(
                id="result",
                title="result",
                inputs={
                    "run_id": InputBinding(
                        source="step", node_id="coding", path=["run_id"], type="string"
                    )
                },
            ),
        ],
        edges=[
            ScenarioEdge(source="input", target="coding"),
            ScenarioEdge(source="coding", target="result"),
        ],
    )


def test_coding_is_v2_only_and_requires_independent_qa():
    assert validate_workflow(graph()).valid
    assert "coding_task_requires_schema_v2" in {i.code for i in validate_workflow(graph(1)).issues}
    configured = graph()
    configured.nodes[1].qa_agent_id = "dev"
    assert "independent_qa_agent_required" in {i.code for i in validate_workflow(configured).issues}


@pytest.mark.parametrize(
    "auth",
    [
        {},
        {"auth_kind": "personal_access_token", "entry_source": "web_scenario"},
        {"auth_kind": "session", "entry_source": "mattermost"},
        {"auth_kind": "oauth_access_token", "entry_source": "web_scenario"},
    ],
)
def test_coding_step_rejects_untrusted_channel_before_admission(auth):
    coding = Mock()
    repository = Mock()
    repository.load_run.return_value = SimpleNamespace(agent_snapshots={"_coding_auth": auth})
    context = SimpleNamespace(run_id="parent", node_id="coding", user=Mock())
    with pytest.raises(WorkflowError, match="channel_not_allowed"):
        asyncio.run(
            ScenarioCodingStepRunner(coding, repository).run(
                graph().nodes[1], {"task": "Test"}, context
            )
        )
    coding.create.assert_not_called()


def test_web_scenario_starts_shared_coding_use_case_and_exposes_background_link():
    coding = Mock()
    coding.create.return_value = SimpleNamespace(run=SimpleNamespace(id="child-run"))
    coding.view.return_value = SimpleNamespace(
        run=SimpleNamespace(id="child-run", status="succeeded", candidate=None),
        report=None,
        receipt=None,
    )
    repository = Mock()
    repository.load_run.return_value = SimpleNamespace(
        scenario_id="scenario",
        agent_snapshots={"_coding_auth": {"auth_kind": "session", "entry_source": "web_scenario"}},
    )
    context = SimpleNamespace(run_id="parent", node_id="coding", attempt=1, user=Mock())
    output = asyncio.run(
        ScenarioCodingStepRunner(coding, repository).run(
            graph().nodes[1], {"task": "Implement net_revenue"}, context
        )
    )
    assert output == {
        "run_id": "child-run",
        "status": "succeeded",
        "report": None,
        "candidate": None,
        "mr_url": None,
    }
    assert coding.create.call_args.kwargs["source"] == "web_scenario"
    assert coding.create.call_args.kwargs["idempotency_key"] == "scenario:parent:coding"
    repository.update_diagnostics.assert_called_once_with(
        "parent", "coding", 1, {"coding_run_id": "child-run"}
    )
