from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.scenario_workflows import LlmNode
from rhetorica_backend.domain.files.models import StoredObject
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.llm_step import ScenarioLlmStepRunner
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext

from agents.usage import Usage


@pytest.mark.parametrize("authoring,expected_tokens", [(False, 4000), (True, 12000)])
def test_editor_uses_connector_budget_without_changing_execution_agent_limits(
    monkeypatch, authoring, expected_tokens
):
    connection = LlmConnectionConfig(
        base_url="https://llm.invalid/v1", model="test", max_tokens=12000
    )
    snapshot = {
        "agent": {"id": "agent", "settings": {"max_tokens": 4000}, "admin_prompt": "ANALYST ONLY"},
        "llm_connector_name": "test",
        "llm_config": connection.model_dump(exclude={"api_key", "api_keys", "headers"}),
    }
    captured = []

    async def run(agent, *_args, **_kwargs):
        captured.append(agent)
        return SimpleNamespace(final_output="done", context_wrapper=SimpleNamespace(usage=Usage()))

    monkeypatch.setattr("rhetorica_backend.domain.scenarios.llm_step.Runner.run", run)
    resources = SimpleNamespace(agent=Mock(), llm_connection=lambda *_args: ("test", connection))
    runner = ScenarioLlmStepRunner(resources, Mock(), Mock())
    context = ScenarioStepContext(
        "proposal", "proposal", "operation", 1, SimpleNamespace(), "", snapshot
    )
    asyncio.run(
        runner.run(
            LlmNode(id="proposal", title="Editor", instructions="Edit"),
            {},
            context,
            record_diagnostics=False,
            authoring=authoring,
        )
    )
    assert captured[0].model_settings.max_tokens == expected_tokens
    assert ("ANALYST ONLY" in captured[0].instructions) is not authoring
    assert snapshot["agent"]["settings"]["max_tokens"] == 4000


def test_saved_llm_response_survives_diagnostics_failure_without_second_model_call(monkeypatch):
    objects = {}

    class Storage:
        def upload_fileobj(self, *, key, fileobj, **_kwargs):
            objects[key] = fileobj.read()

        def open(self, key):
            if key not in objects:
                raise FileNotFoundError(key)
            return StoredObject(io.BytesIO(objects[key]), len(objects[key]), "application/json")

    class Repository:
        attempts = 0
        diagnostics = None

        def update_diagnostics(self, *_args):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("database temporarily unavailable")
            self.diagnostics = _args[-1]

    connection = LlmConnectionConfig(base_url="http://llm.invalid/v1", model="test", api_key="test")
    resources = SimpleNamespace(
        agent=lambda *_args: None,
        llm_connection=lambda *_args: ("test", connection),
    )
    snapshot = {
        "agent": {"id": "agent", "settings": {}},
        "llm_connector_name": "test",
        "llm_config": connection.model_dump(
            mode="json", exclude={"api_key", "api_keys", "headers"}
        ),
    }
    calls = []

    async def run(*_args, **_kwargs):
        calls.append(True)
        return SimpleNamespace(
            final_output="1200 rows processed",
            context_wrapper=SimpleNamespace(
                usage=Usage(requests=1, input_tokens=12, output_tokens=8, total_tokens=20)
            ),
        )

    monkeypatch.setattr("rhetorica_backend.domain.scenarios.llm_step.Runner.run", run)
    repository = Repository()
    runner = ScenarioLlmStepRunner(resources, repository, ScenarioDataStore(Storage()))
    context = ScenarioStepContext(
        "run",
        "llm",
        "operation",
        1,
        SimpleNamespace(id="user", workspace_id="default"),
        "conversation",
        snapshot,
    )
    node = LlmNode(id="llm", title="Summary", agent_id="agent")
    with pytest.raises(RuntimeError, match="database"):
        asyncio.run(runner.run(node, {"rows": 1200}, context))
    assert asyncio.run(runner.run(node, {"rows": 1200}, context)) == {
        "result": "1200 rows processed"
    }
    assert len(calls) == 1
    assert json.loads(json.dumps(repository.diagnostics))["usage"]["total_tokens"] == 20

    def revoked(*_args):
        raise WorkflowError("scenario_agent_unavailable", status_code=403)

    resources.agent = revoked
    with pytest.raises(WorkflowError, match="unavailable"):
        asyncio.run(runner.run(node, {}, context))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "content,valid", [(json.dumps({"summary": "done"}), True), ("not JSON", False)]
)
def test_plain_text_authoring_is_parsed_and_validated_without_enabling_connector_json(
    monkeypatch, content, valid
):
    from rhetorica_backend.contracts.scenario_workflows import ValueSpec

    from agents import ModelBehaviorError

    connection = LlmConnectionConfig(
        base_url="https://llm.invalid/v1",
        model="test",
        extra_settings={"structured_output_enabled": False},
    )
    snapshot = {
        "llm_connector_name": "test",
        "llm_config": connection.model_dump(exclude={"api_key", "api_keys", "headers"}),
    }
    captured = []

    async def run(agent, *_args, **_kwargs):
        captured.append(agent)
        return SimpleNamespace(final_output=content, context_wrapper=SimpleNamespace(usage=Usage()))

    monkeypatch.setattr("rhetorica_backend.domain.scenarios.llm_step.Runner.run", run)
    runner = ScenarioLlmStepRunner(
        SimpleNamespace(llm_connection=lambda *_a: ("test", connection)), Mock(), Mock()
    )
    node = LlmNode(
        id="proposal",
        title="Editor",
        output_format="json",
        outputs={
            "result": ValueSpec(
                type="json", json_schema={"type": "object", "required": ["summary"]}
            )
        },
    )
    context = ScenarioStepContext(
        "proposal", "proposal", "operation", 1, SimpleNamespace(), "", snapshot
    )
    if valid:
        assert asyncio.run(runner.run(node, {}, context, record_diagnostics=False, authoring=True))[
            "result"
        ] == {"summary": "done"}
    else:
        with pytest.raises(ModelBehaviorError):
            asyncio.run(runner.run(node, {}, context, record_diagnostics=False, authoring=True))
    assert captured[0].output_type is None
    assert connection.extra_settings == {"structured_output_enabled": False}
    with pytest.raises(WorkflowError, match="structured_output_not_supported"):
        asyncio.run(runner.run(node, {}, context, record_diagnostics=False))


def test_authoring_snapshots_keep_access_checks_but_do_not_require_structured_mode():
    from rhetorica_backend.contracts.scenario_workflows import ScenarioWorkflowDefinition
    from rhetorica_backend.domain.scenarios.resources import ScenarioResourcePolicy

    policy = ScenarioResourcePolicy(Mock(), Mock(), Mock(), Mock())
    connection = LlmConnectionConfig(
        base_url="https://llm.invalid",
        model="test",
        extra_settings={"structured_output_enabled": False},
    )
    user = SimpleNamespace()
    policy.agent = Mock(
        return_value=SimpleNamespace(
            llm_connector_name="test", model_dump=Mock(return_value={"id": "agent"})
        )
    )
    policy.llm_connection = Mock(return_value=("test", connection))
    graph = ScenarioWorkflowDefinition(
        nodes=[LlmNode(id="proposal", title="Editor", agent_id="agent", output_format="json")]
    )
    assert policy.snapshots(graph, user, authoring=True)["proposal"]["agent"]["id"] == "agent"
    policy.agent.assert_called_once_with("agent", user)
    with pytest.raises(WorkflowError, match="structured_output_not_supported"):
        policy.snapshots(graph, user)
    policy.agent.side_effect = WorkflowError("scenario_agent_unavailable", status_code=403)
    with pytest.raises(WorkflowError, match="scenario_agent_unavailable"):
        policy.snapshots(graph, user, authoring=True)
