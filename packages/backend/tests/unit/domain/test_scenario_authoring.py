from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from rhetorica_backend.contracts.scenario_workflows import (
    InputNode,
    ResultNode,
    ScenarioAiProposalRequest,
    ScenarioDraft,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
    WorkflowValidationResult,
)
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.workflow_service import ScenarioWorkflowService

from agents import ModelBehaviorError


def service(error=None):
    graph = ScenarioWorkflowDefinition(
        nodes=[InputNode(id="input", title="Input"), ResultNode(id="result", title="Result")],
        edges=[ScenarioEdge(source="input", target="result")],
    )
    authoring = SimpleNamespace(
        get_draft=Mock(
            return_value=ScenarioDraft(scenario_id="scenario", revision=3, workflow=graph)
        )
    )
    resources = SimpleNamespace(
        snapshots=Mock(return_value={"proposal": {}}), capabilities=Mock(return_value={})
    )
    llm = SimpleNamespace(
        run=AsyncMock(
            side_effect=error,
            return_value={
                "result": {
                    "summary": "Переименовал результат",
                    "complete": True,
                    "operations": [
                        {"op": "replace", "path": "/nodes/result/title", "value": "Готово"}
                    ],
                }
            },
        )
    )
    app = ScenarioWorkflowService(authoring, Mock(), resources, Mock(), Mock(), llm, enabled=True)
    app.validate = Mock(return_value=WorkflowValidationResult(valid=True))
    return app


def test_authoring_is_a_proposal_with_an_isolated_editor_prompt_and_no_diagnostics_writes():
    app = service()
    result = asyncio.run(
        app.propose(
            "scenario",
            SimpleNamespace(),
            ScenarioAiProposalRequest(
                agent_id="agent", instruction="Добавь шаг", expected_revision=3
            ),
        )
    )
    assert result.validation.valid
    assert result.base_revision == 3
    assert app.llm.run.call_args.kwargs == {"record_diagnostics": False, "authoring": True}
    assert app.authoring.get_draft.call_count == 1


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (
            ModelBehaviorError("Unterminated string, private response"),
            "scenario_ai_invalid_output",
            422,
        ),
        (
            APITimeoutError(request=httpx2.Request("POST", "https://llm.invalid")),
            "scenario_ai_timeout",
            504,
        ),
        (
            APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid")),
            "scenario_ai_unavailable",
            502,
        ),
        (
            APIStatusError(
                "private provider response",
                response=httpx2.Response(
                    429, request=httpx2.Request("POST", "https://llm.invalid")
                ),
                body=None,
            ),
            "scenario_ai_provider_error",
            502,
        ),
    ],
)
def test_authoring_errors_are_safe_typed_errors_with_bounded_repair_and_connection_retry(
    exception, code, status
):
    app = service(exception)
    with pytest.raises(WorkflowError) as caught:
        asyncio.run(
            app.propose(
                "scenario",
                SimpleNamespace(),
                ScenarioAiProposalRequest(
                    agent_id="agent", instruction="Добавь шаг", expected_revision=3
                ),
            )
        )
    assert caught.value.code == code
    assert caught.value.status_code == status
    assert "private" not in str(caught.value)
    assert app.llm.run.call_count == (
        3
        if isinstance(exception, ModelBehaviorError)
        else 2
        if isinstance(exception, APIConnectionError) and not isinstance(exception, APITimeoutError)
        else 1
    )
    app.validate.assert_not_called()


def test_invalid_generated_contract_does_not_reach_graph_validation():
    app = service()
    app.llm.run.return_value = {"result": {"nodes": [{"type": "unknown"}]}}
    with pytest.raises(WorkflowError, match="scenario_ai_invalid_output"):
        asyncio.run(
            app.propose(
                "scenario",
                SimpleNamespace(),
                ScenarioAiProposalRequest(
                    agent_id="agent", instruction="Добавь шаг", expected_revision=3
                ),
            )
        )
    app.validate.assert_not_called()
