import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.domain.coding.context import CodingContextController
from rhetorica_backend.domain.coding.qa_verdict import CodingQaVerdictTool, accepted_verdict

from agents import ModelSettings
from agents.run_config import ModelInputData


def controller(journal=None):
    result = CodingContextController(
        model=Mock(),
        model_settings=ModelSettings(),
        budget=LlmPromptBudgetService(
            model_name="unknown", context_window_tokens=1000, response_reserve_tokens=100
        ),
        original_task="Exact original task",
        trigger_ratio=0.8,
        journal=journal,
        run_id="run",
        task_id="qa",
        operation_id="qa:0",
    )
    result._summarize = AsyncMock(return_value="Tests passed; materialization not_run.")
    return result


def data(items):
    return SimpleNamespace(
        agent=SimpleNamespace(tools=[]),
        model_data=ModelInputData(input=items, instructions="Authoritative instructions"),
    )


def test_context_below_threshold_does_not_spend_a_compaction_call():
    policy = controller()
    request = data([{"role": "user", "content": "small task"}])
    prepared = asyncio.run(policy(request))
    assert prepared.input == request.model_data.input
    policy._summarize.assert_not_awaited()


def test_checkpoint_preserves_task_instructions_and_new_tool_protocol_suffix():
    policy = controller()
    items = [{"role": "user", "content": "x" * 1800}]
    compact = asyncio.run(policy(data(items)))
    assert compact.input[0]["content"] == "Exact original task"
    assert "materialization not_run" in compact.input[1]["content"]
    assert compact.instructions == "Authoritative instructions"
    assert items[0]["content"] == "x" * 1800
    suffix = [
        {"type": "function_call", "call_id": "c", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "ok"},
    ]
    prepared = asyncio.run(policy(data([*items, *suffix])))
    assert prepared.input[-2:] == suffix
    policy._summarize.assert_awaited_once()


def test_checkpoint_replay_after_restart_does_not_call_the_paid_model_again():
    entries = {}
    journal = SimpleNamespace(
        get=lambda **s: entries.get(tuple(s.values())),
        put=lambda value, **s: entries.update({tuple(s.values()): value}),
    )
    first, restarted = controller(journal), controller(journal)
    request = data([{"role": "user", "content": "x" * 1800}])
    assert asyncio.run(first(request)).input == asyncio.run(restarted(request)).input
    restarted._summarize.assert_not_awaited()


def test_qa_completion_is_strict_and_authorized_before_returning_a_verdict():
    authorize = Mock()
    tool = CodingQaVerdictTool(authorize).build()
    result = asyncio.run(
        tool.on_invoke_tool(None, '{"status":"passed","reason":"Two tests passed"}')
    )
    assert '"status":"passed"' in result
    authorize.assert_called_once()
    rejected = asyncio.run(
        tool.on_invoke_tool(None, '{"status":"passed","reason":"ok","permissions":"write"}')
    )
    assert "permissions" in rejected
    assert accepted_verdict(rejected) is None


def test_oversized_compaction_fails_before_a_paid_model_call():
    policy = controller()
    del policy._summarize
    with pytest.raises(ValueError, match="coding_compaction_input_exceeds_context"):
        asyncio.run(policy._summarize("x" * 10000))


@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_native_compaction_uses_connector_policy_without_manual_summary(with_checkpoint):
    from openai.types.responses import ResponseCompactionItem

    policy = controller()
    policy._settings = ModelSettings(
        context_management=[{"type": "compaction", "compact_threshold": 180000}]
    )
    prefix = [{"role": "user", "content": "x" * 10000}]
    suffix = [
        ResponseCompactionItem(id="checkpoint", type="compaction", encrypted_content="opaque"),
        {"type": "function_call", "call_id": "c", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "result"},
    ]
    items = prefix + suffix if with_checkpoint else prefix
    prepared = asyncio.run(policy(data(items)))
    assert prepared.input == (suffix if with_checkpoint else prefix)
    assert prepared.instructions == "Authoritative instructions"
    policy._summarize.assert_not_awaited()


def test_old_tool_results_are_retrievable_receipts_without_mutating_sdk_session():
    import json

    policy = controller(journal=Mock())
    policy._retained_tool_output_tokens = 1000
    items = [
        {
            "type": "function_call_output",
            "call_id": "old",
            "output": json.dumps({"stdout": "x" * 6000, "exit_code": 0}),
        },
        {
            "type": "function_call_output",
            "call_id": "new",
            "output": json.dumps({"stdout": "y" * 1600}),
        },
    ]
    projected = policy._project_tool_outputs(items)
    receipt = json.loads(projected[0]["output"])
    assert receipt["tool_result"]["call_id"] == "old"
    assert receipt["exit_code"] == 0
    assert len(projected[0]["output"]) <= 512
    assert projected[1] == items[1]
    assert len(items[0]["output"]) > 6000


def test_context_cost_ceiling_compacts_before_a_large_provider_window_fills():
    policy = controller()
    policy._budget = LlmPromptBudgetService(model_name="unknown", context_window_tokens=200000)
    policy._max_prompt_tokens = 1000
    asyncio.run(policy(data([{"role": "user", "content": "x" * 3000}])))
    policy._summarize.assert_awaited_once()
