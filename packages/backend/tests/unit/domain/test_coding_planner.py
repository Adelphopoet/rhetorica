import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.coding import CodingPlanDraft, CodingResourceSelection
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.planner import (
    CodingCoordinatorPlanner,
    finish_on_accepted_plan,
)
from rhetorica_backend.domain.coding.planning_model import CodingPlanningModel


@pytest.fixture
def planning_inputs():
    service = Mock()
    service.root_policy.return_value.max_iterations = 200
    llms = Mock()
    llms.get_connection.return_value = LlmConnectionConfig(
        base_url="http://unused.invalid/v1", model="test", api_key="test"
    )
    profile = SimpleNamespace(
        id="root",
        title="Coordinator",
        admin_prompt="Groom the task",
        llm_connector_name="selected-llm",
        settings=SimpleNamespace(temperature=1, max_tokens=32000),
    )
    resources = CodingResourceSelection(
        root_agent_id="root",
        developer_agent_id="dev",
        qa_agent_id="qa",
        gitlab_connector_id="gitlab",
        project_id="group/project",
        target_branch="main",
        environment_profile_id="env",
    )
    proposal = CodingPlanDraft(
        task="Add test",
        steps=["Inspect", "Test"],
        developer_brief="Add a dbt test",
        qa_brief="Run independently",
        acceptance_criteria=["Test passes"],
    )

    return service, llms, profile, resources, proposal


def test_selected_coordinator_has_only_strict_read_and_proposal_tools_without_paid_calls(
    planning_inputs, monkeypatch
):
    service, llms, profile, resources, proposal = planning_inputs
    monkeypatch.setattr("rhetorica_backend.domain.coding.planner.get_max_iterations", lambda: 100)

    async def runner(agent, messages, **kwargs):
        assert kwargs["max_turns"] == 100
        assert kwargs["run_config"].tracing_disabled
        assert kwargs["run_config"].call_model_input_filter is not None
        assert agent.model_settings.temperature == 1
        assert agent.model_settings.max_tokens == 32000
        assert agent.model_settings.parallel_tool_calls is False
        assert agent.tool_use_behavior is finish_on_accepted_plan
        assert {t.name for t in agent.tools} == {
            "propose_coding_plan",
            "planning_read_file",
            "planning_list_tree",
        }
        assert all(t.strict_json_schema for t in agent.tools)
        tool = next(t for t in agent.tools if t.name == "propose_coding_plan")
        await tool.on_invoke_tool(None, proposal.model_dump_json())
        return SimpleNamespace(final_output="proposal")

    planner = CodingCoordinatorPlanner(service=service, llms=llms, runner=runner)
    result, answer = asyncio.run(
        planner.discuss(
            user=SimpleNamespace(workspace_id="ws"),
            profile=profile,
            resources=resources,
            messages=[{"role": "user", "content": "Add a dbt test"}],
        )
    )
    assert result == proposal
    assert "Поручение разработки" in answer and "Независимая проверка" in answer
    service.create.assert_not_called()
    service.root_policy.assert_not_called()
    service.validate_resources.assert_called_once()
    llms.get_connection.assert_called_once_with("ws", "selected-llm")


def test_oversized_plan_is_rejected_before_confirmation():
    with pytest.raises(ValidationError, match="coding_plan_instruction_too_long"):
        CodingPlanDraft(
            task="t" * 4000,
            steps=["s" * 250] * 12,
            developer_brief="d" * 2500,
            qa_brief="q" * 1500,
            acceptance_criteria=["a" * 250] * 8,
        )


def test_invalid_plan_tool_arguments_are_repairable_in_the_same_discussion(
    planning_inputs, monkeypatch
):
    service, llms, profile, resources, _ = planning_inputs
    monkeypatch.setattr("rhetorica_backend.domain.coding.planner.get_max_iterations", lambda: 100)

    async def runner(agent, messages, **kwargs):
        tool = next(item for item in agent.tools if item.name == "propose_coding_plan")
        response = await tool.on_invoke_tool(None, '{"task":"Add a test"}')
        parsed = json.loads(response)
        assert parsed["status"] == "invalid_arguments"
        assert parsed["reason_code"] == "coding_plan_invalid"
        assert parsed["errors"]
        return SimpleNamespace(final_output="Please repair the plan.")

    planner = CodingCoordinatorPlanner(service=service, llms=llms, runner=runner)
    draft, answer = asyncio.run(
        planner.discuss(
            user=SimpleNamespace(workspace_id="ws"),
            profile=profile,
            resources=resources,
            messages=[{"role": "user", "content": "Add a dbt test"}],
        )
    )

    assert draft is None
    assert answer == "Please repair the plan."
    service.create.assert_not_called()


def test_planning_and_compaction_share_a_non_resetting_call_budget():
    streamed = []

    async def stream_response(**kwargs):
        streamed.append(kwargs)
        yield "delta"

    delegate = SimpleNamespace(
        get_response=AsyncMock(return_value="answer"), stream_response=stream_response
    )
    model = CodingPlanningModel(delegate, max_calls=2)

    async def exercise():
        assert [event async for event in model.stream_response(input="discussion")] == ["delta"]
        await model.get_response(input="compaction")
        with pytest.raises(CodingRunError, match="coding_planning_iteration_limit_reached"):
            await model.get_response(input="next discussion")
        with pytest.raises(CodingRunError, match="coding_planning_iteration_limit_reached"):
            _ = [event async for event in model.stream_response(input="next streamed discussion")]

    asyncio.run(exercise())
    assert delegate.get_response.await_count == 1
    assert len(streamed) == 1


@pytest.mark.parametrize("chat_limit, expected_calls, succeeds", [(100, 13, True), (6, 6, False)])
def test_sdk_discussion_can_pass_twelve_reads_and_respects_ordinary_chat_budget(
    planning_inputs, monkeypatch, chat_limit, expected_calls, succeeds
):
    from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall

    from agents.items import ModelResponse
    from agents.usage import Usage

    service, llms, profile, resources, proposal = planning_inputs
    # Coding policy is deliberately smaller than this chat's read sequence.
    service.root_policy.return_value.max_iterations = 2
    service.gitlab.read_file.return_value = {"content": "Repository guidance"}
    calls = []

    async def response(*args, **kwargs):
        calls.append(kwargs)
        turn = len(calls)
        proposing = turn == 13
        return ModelResponse(
            output=[
                ResponseFunctionToolCall(
                    id=f"tool-{turn}",
                    call_id=f"call-{turn}",
                    type="function_call",
                    name="propose_coding_plan" if proposing else "planning_read_file",
                    arguments=proposal.model_dump_json()
                    if proposing
                    else '{"path":"AGENTS.md","start_line":1,"end_line":300}',
                )
            ],
            usage=Usage(requests=1),
            response_id=f"response-{turn}",
        )

    async def stream_response(*args, **kwargs):
        result = await response(*args, **kwargs)
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=0,
            response=Response(
                id=result.response_id,
                created_at=0,
                model="test",
                object="response",
                output=result.output,
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                status="completed",
            ),
        )

    model = SimpleNamespace(get_response=response, stream_response=stream_response)
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.llm.sdk_models.build_sdk_model",
        lambda **kwargs: model,
    )
    monkeypatch.setattr(
        "rhetorica_backend.domain.coding.planner.get_max_iterations", lambda: chat_limit
    )
    planner = CodingCoordinatorPlanner(service=service, llms=llms)
    events = []
    discussion = planner.discuss(
        user=SimpleNamespace(workspace_id="ws"),
        profile=profile,
        resources=resources,
        messages=[{"role": "user", "content": "Count unique scenarios daily"}],
        on_event=events.append,
    )
    if succeeds:
        draft, answer = asyncio.run(discussion)
        assert draft == proposal and "Поручение разработки" in answer
    else:
        with pytest.raises(CodingRunError, match="coding_planning_iteration_limit_reached"):
            asyncio.run(discussion)
    assert len(calls) == expected_calls
    starts = [event for event in events if event.type.value == "tool_call_start"]
    ends = [event for event in events if event.type.value == "tool_call_end"]
    assert len(starts) == len(ends) == expected_calls
    assert [event.tool_call_id for event in starts] == [event.tool_call_id for event in ends]
    assert "Repository guidance" not in str(events)
    assert not any(event.type.value == "thinking" for event in events)
    if succeeds:
        assert ends[-1].tool_name == "propose_coding_plan" and ends[-1].success
    service.root_policy.assert_not_called()
    service.create.assert_not_called()


def test_model_timeout_has_stable_planning_error(planning_inputs):
    import httpx2
    from openai import APITimeoutError

    service, llms, profile, resources, _ = planning_inputs
    runner = AsyncMock(side_effect=APITimeoutError(request=httpx2.Request("POST", "http://unused")))
    planner = CodingCoordinatorPlanner(service=service, llms=llms, runner=runner)
    with pytest.raises(CodingRunError, match="coding_planning_timeout"):
        asyncio.run(
            planner.discuss(
                user=SimpleNamespace(workspace_id="ws"),
                profile=profile,
                resources=resources,
                messages=[{"role": "user", "content": "Plan"}],
            )
        )
    runner.assert_awaited_once()
    service.create.assert_not_called()


def test_native_streaming_clarification_appears_as_chat_chunks_without_a_plan(
    planning_inputs, monkeypatch
):
    from openai.types.responses import (
        Response,
        ResponseCompletedEvent,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseTextDeltaEvent,
    )

    service, llms, profile, resources, _ = planning_inputs
    release = asyncio.Event()
    answer = "Какой период считаем?"

    async def stream_response(*args, **kwargs):
        yield ResponseTextDeltaEvent(
            type="response.output_text.delta",
            delta=answer,
            item_id="message",
            content_index=0,
            output_index=0,
            sequence_number=0,
            logprobs=[],
        )
        await release.wait()
        yield ResponseCompletedEvent(
            type="response.completed",
            sequence_number=1,
            response=Response(
                id="response",
                created_at=0,
                model="test",
                object="response",
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                status="completed",
                output=[
                    ResponseOutputMessage(
                        id="message",
                        role="assistant",
                        status="completed",
                        type="message",
                        content=[
                            ResponseOutputText(
                                type="output_text", text=answer, annotations=[], logprobs=[]
                            )
                        ],
                    )
                ],
            ),
        )

    model = SimpleNamespace(stream_response=stream_response)
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.llm.sdk_models.build_sdk_model",
        lambda **kwargs: model,
    )
    events = []

    def observe(event):
        events.append(event)
        if event.type.value == "chunk":
            release.set()

    planner = CodingCoordinatorPlanner(service=service, llms=llms)
    draft, text = asyncio.run(
        planner.discuss(
            user=SimpleNamespace(workspace_id="ws"),
            profile=profile,
            resources=resources,
            messages=[{"role": "user", "content": "Count scenarios"}],
            on_event=observe,
        )
    )
    assert draft is None and text == answer
    assert any(event.type.value == "chunk" and event.content == answer for event in events)
    service.create.assert_not_called()


def test_planning_accounts_streamed_usage_before_yield_and_compaction_calls():
    from agents.items import ModelResponse
    from agents.usage import Usage

    usage_callback = AsyncMock()
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=3,
        total_tokens=13,
        model_dump=lambda **_: {"input_tokens": 10, "output_tokens": 3},
    )
    event = SimpleNamespace(
        type="response.completed", response=SimpleNamespace(id="stream", usage=usage)
    )

    async def stream_response(**kwargs):
        yield event
        yield event  # Terminal duplicates never double bill the conversation.

    compact = ModelResponse(
        output=[], response_id="compact", usage=Usage(input_tokens=7, output_tokens=2)
    )
    model = CodingPlanningModel(
        SimpleNamespace(
            get_response=AsyncMock(return_value=compact), stream_response=stream_response
        ),
        max_calls=2,
        on_usage=usage_callback,
    )

    async def exercise():
        async for _ in model.stream_response(input="discussion"):
            assert usage_callback.await_count == 1
        await model.get_response(input="compaction")

    asyncio.run(exercise())
    assert usage_callback.await_count == 2
    assert usage_callback.await_args_list[0].args[0].usage.input_tokens == 10
    assert usage_callback.await_args_list[1].args[0] is compact


def test_rejected_plan_returns_to_the_model_instead_of_ending_the_discussion():
    from types import SimpleNamespace

    from rhetorica_backend.domain.coding.planner import finish_on_accepted_plan

    tool = SimpleNamespace(name="propose_coding_plan")
    rejected = SimpleNamespace(
        tool=tool, output='{"status": "invalid_arguments", "reason_code": "coding_plan_invalid"}'
    )
    accepted = SimpleNamespace(tool=tool, output='{"task": "Add net_revenue"}')
    read = SimpleNamespace(tool=SimpleNamespace(name="repository_read"), output="{}")

    assert finish_on_accepted_plan(None, [rejected]).is_final_output is False
    assert finish_on_accepted_plan(None, [read]).is_final_output is False
    result = finish_on_accepted_plan(None, [accepted])
    assert result.is_final_output is True
    assert result.final_output == accepted.output
