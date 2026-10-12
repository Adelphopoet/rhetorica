"""QA finishes only through submit_qa_verdict when the last turn is not a JSON schema."""

import asyncio
import json

import pytest
from rhetorica_backend.domain.coding.qa_verdict import (
    QA_VERDICT_MISSING,
    CodingQaVerdictTool,
    QaResult,
    configure_unstructured_qa,
    extract_qa_verdict,
)

from agents import Agent, ModelSettings, RunConfig, function_tool
from agents.exceptions import MaxTurnsExceeded
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool_context import ToolContext


@function_tool
def candidate_diff() -> str:
    """Show the candidate diff against the base."""
    return "+Проверка coding"


def _review(steps, *, max_turns=4):
    model = ScriptedModel(steps)
    agent = Agent(
        name="qa",
        instructions="Review the candidate.",
        model=model,
        model_settings=ModelSettings(),
        tools=[candidate_diff, CodingQaVerdictTool(lambda: None).build()],
    )
    configure_unstructured_qa(agent)
    specialist = agent.as_tool(
        tool_name="agent_qa",
        tool_description="QA",
        custom_output_extractor=extract_qa_verdict,
        max_turns=max_turns,
        run_config=RunConfig(tracing_disabled=True),
        failure_error_function=None,
    )
    arguments = json.dumps({"input": "review"})
    context = ToolContext(
        context=None, tool_name=specialist.name, tool_call_id="op", tool_arguments=arguments
    )
    return asyncio.run(specialist.on_invoke_tool(context, arguments)), model


def test_review_inspects_then_finishes_on_submit_qa_verdict():
    output, model = _review(
        [
            [function_call("candidate_diff", {}, call_id="diff")],
            [
                function_call(
                    "submit_qa_verdict",
                    {"status": "passed", "reason": "Файл содержит одну строку Проверка coding"},
                    call_id="verdict",
                )
            ],
        ]
    )

    assert QaResult.model_validate_json(output) == QaResult(
        status="passed", reason="Файл содержит одну строку Проверка coding"
    )
    assert [call.model_settings.tool_choice for call in model.calls] == ["required", "required"]


def test_rejected_verdict_returns_its_error_to_the_model():
    output, model = _review(
        [
            [function_call("submit_qa_verdict", {"status": "ok"}, call_id="rejected")],
            [
                function_call(
                    "submit_qa_verdict",
                    {"status": "failed", "reason": "Тест не прошёл"},
                    call_id="verdict",
                )
            ],
        ]
    )

    assert QaResult.model_validate_json(output) == QaResult(
        status="failed", reason="Тест не прошёл"
    )
    rejected = next(
        item
        for item in model.calls[1].input
        if isinstance(item, dict) and item.get("call_id") == "rejected" and "output" in item
    )
    assert "Input should be 'passed', 'failed' or 'blocked'" in str(rejected["output"])


def test_message_with_verdict_json_is_not_a_verdict():
    output, _model = _review(
        [
            [assistant_message('{"status":"passed","reason":"Inspecting the candidate"}')],
        ]
    )

    assert QaResult.model_validate_json(output) == QaResult(
        status="blocked", reason=QA_VERDICT_MISSING
    )


def test_review_without_verdict_stops_at_the_turn_limit():
    with pytest.raises(MaxTurnsExceeded):
        _review(
            [[function_call("candidate_diff", {}, call_id=f"diff-{n}")] for n in range(3)],
            max_turns=2,
        )
