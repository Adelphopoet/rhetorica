from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseInProgressEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_reasoning_item import Summary
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.files import FileArtifactRef
from rhetorica_backend.contracts.visualization import VisualizationSpec
from rhetorica_backend.domain.chat.orchestration.agent_runtime import (
    AgentRuntimeInput,
    AgentRuntimePreparedContent,
    AgentRuntimeRunItemEvent,
    AgentRuntimeTransport,
)
from rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime import (
    AgentsSdkCapabilityError,
    AgentsSdkTurnRuntime,
    _CredentialAwareSdkModel,
    _FunctionToolRegistry,
    _SdkCompactionRestart,
    _SdkModelInputController,
    _SdkTurnControl,
    build_agent_run_config,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    ChatFinalResponseContractV1,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.models import ExecuteToolCallPlan
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_execution_policy import (
    ToolExecutionPolicy,
)

from agents import MaxTurnsExceeded, ModelSettings, RunConfig
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.run_config import CallModelData, ModelInputData
from agents.usage import Usage


def test_agents_sdk_tracing_stays_enabled_for_langfuse_instrumentation(monkeypatch) -> None:
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.langfuse_enabled",
        lambda: True,
    )

    run_config = build_agent_run_config()

    assert run_config.tracing_disabled is False


def test_agents_sdk_tracing_is_disabled_without_active_langfuse_instrumentation(monkeypatch) -> None:
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.langfuse_enabled",
        lambda: False,
    )

    run_config = build_agent_run_config()

    assert run_config.tracing_disabled is True


class _ToolExecutionPolicy:
    def plan_tool_call(self, **_kwargs: Any) -> Any:
        return ExecuteToolCallPlan(
            tool_call_id=_kwargs["tool_call_id"],
            call_signature=(_kwargs["tool_name"], _kwargs["args_json"]),
        )


class _LlmClient:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_key="test-key",
        temperature=0,
        max_tokens=256,
    )


class _PlainTextLlmClient:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_key="test-key",
        temperature=0,
        max_tokens=256,
        extra_settings={"structured_output_enabled": False},
    )


class _ThinkingPlainTextLlmClient:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        endpoint_path="/chat/completions",
        model="test-model",
        api_key="test-key",
        temperature=0,
        max_tokens=256,
        extra_settings={
            "structured_output_enabled": False,
            "chat_template_enable_thinking": True,
        },
    )


class _ToolExecutor:
    def execute_tool(self, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("no tools expected")


class _RecordingToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_tool(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ok": True, "value": kwargs["arguments"]["value"]}


def test_credential_aware_sdk_model_switches_key_after_transient_error() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args, kwargs
            calls.append(self.index)
            if self.index == 0:
                raise httpx.ReadError(
                    "temporary",
                    request=httpx.Request("POST", "https://llm.example.test"),
                )
            return ModelResponse(
                output=[],
                usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2),
                response_id="ok",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    model = _CredentialAwareSdkModel(
        connection=connection,
        model_factory=_Model,
        max_attempts=2,
    )
    response = asyncio.run(
        model.get_response(
            None,
            [],
            ModelSettings(),
            [],
            None,
            [],
            "disabled",
        )
    )

    assert response.response_id == "ok"
    assert calls == [0, 1]
    assert connection.selected_api_key_index == 1
    assert connection.api_key == "key-b"


def test_credential_retry_rebind_is_reused_by_later_model_calls() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    rebinds: list[tuple[int, int]] = []
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
        rebind=lambda selected, expected: rebinds.append((selected, expected)) or True,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            calls.append(self.index)
            if len(calls) == 1:
                raise httpx.ReadError(
                    "temporary",
                    request=httpx.Request("POST", "https://llm.example.test"),
                )
            return ModelResponse(
                output=[],
                usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2),
                response_id="ok",
            )

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            if False:
                yield None

    model = _CredentialAwareSdkModel(connection=connection, model_factory=_Model, max_attempts=2)
    args = (None, [], ModelSettings(), [], None, [], "disabled")
    asyncio.run(model.get_response(*args))
    asyncio.run(model.get_response(*args))

    assert calls == [0, 1, 1]
    assert rebinds == [(1, 0)]
    assert connection.selected_api_key_index == 1


@pytest.mark.parametrize(
    ("error_kind", "expected_error"),
    [
        ("non_retryable", httpx.HTTPStatusError),
        ("failed_retry", httpx.ReadError),
    ],
)
def test_credential_retry_failures_do_not_rebind(
    error_kind: str,
    expected_error: type[Exception],
) -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    rebinds: list[tuple[int, int]] = []
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
        rebind=lambda selected, expected: rebinds.append((selected, expected)) or True,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            calls.append(self.index)
            if error_kind == "non_retryable":
                request = httpx.Request("POST", "https://llm.example.test")
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError("unauthorized", request=request, response=response)
            raise httpx.ReadError(
                "temporary",
                request=httpx.Request("POST", "https://llm.example.test"),
            )

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            if False:
                yield None

    model = _CredentialAwareSdkModel(connection=connection, model_factory=_Model, max_attempts=2)
    with pytest.raises(expected_error):
        asyncio.run(model.get_response(None, [], ModelSettings(), [], None, [], "disabled"))

    assert calls == ([0] if error_kind == "non_retryable" else [0, 1])
    assert rebinds == []
    assert connection.selected_api_key_index == 0
    assert connection.api_key is None


def test_credential_stream_retries_only_before_content_and_emits_safe_diagnostics() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a-sensitive", "key-b-sensitive"],
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    calls: list[int] = []
    events: list[dict[str, Any]] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            raise AssertionError("stream path expected")

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            calls.append(self.index)
            if self.index == 0:
                raise httpx.ReadError(
                    "temporary",
                    request=httpx.Request("POST", "https://llm.example.test"),
                )
            yield "completed"

    model = _CredentialAwareSdkModel(
        connection=connection,
        model_factory=_Model,
        max_attempts=2,
        observe_credential=lambda **fields: events.append(fields),
    )

    async def collect() -> list[str]:
        return [event async for event in model.stream_response(None, [], ModelSettings(), [], None, [], "disabled")]

    assert asyncio.run(collect()) == ["completed"]
    assert calls == [0, 1]
    assert connection.selected_api_key_index == 1
    assert events == [
        {
            "attempt": 1,
            "credential_reused": True,
            "credential_switched": False,
            "outcome": "retryable_error",
            "retryable": True,
        },
        {
            "attempt": 2,
            "credential_reused": False,
            "credential_switched": True,
            "outcome": "succeeded",
            "retryable": None,
        },
    ]
    assert "key-a-sensitive" not in str(events)
    assert "key-b-sensitive" not in str(events)
    assert "credential_index" not in str(events)


def test_credential_stream_retries_after_lifecycle_event_before_content() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            raise AssertionError("stream path expected")

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            calls.append(self.index)
            if self.index == 0:
                yield ResponseCreatedEvent.model_construct(type="response.created")
                yield ResponseInProgressEvent.model_construct(type="response.in_progress")
                raise httpx.ReadError(
                    "temporary",
                    request=httpx.Request("POST", "https://llm.example.test"),
                )
            yield "completed"

    model = _CredentialAwareSdkModel(connection=connection, model_factory=_Model, max_attempts=2)

    async def collect() -> list[Any]:
        return [
            event
            async for event in model.stream_response(
                None, [], ModelSettings(), [], None, [], "disabled"
            )
        ]

    streamed_events = asyncio.run(collect())
    assert streamed_events[-1] == "completed"
    assert calls == [0, 1]
    assert connection.selected_api_key_index == 1


@pytest.mark.parametrize(
    "event",
    [
        ResponseTextDeltaEvent.model_construct(
            type="response.output_text.delta",
            delta="text",
        ),
        ResponseReasoningTextDeltaEvent.model_construct(
            type="response.reasoning_text.delta",
            delta="reasoning",
        ),
        ResponseReasoningSummaryTextDeltaEvent.model_construct(
            type="response.reasoning_summary_text.delta",
            delta="summary",
        ),
        ResponseFunctionCallArgumentsDeltaEvent.model_construct(
            type="response.function_call_arguments.delta",
            delta='{"query":"gmv"}',
        ),
        ResponseOutputItemAddedEvent.model_construct(type="response.output_item.added"),
        ResponseCompletedEvent.model_construct(type="response.completed"),
    ],
)
def test_credential_stream_events_other_than_lifecycle_block_retry(event: Any) -> None:
    assert _CredentialAwareSdkModel._stream_event_allows_replay(event) is False


def test_credential_stream_does_not_retry_after_completed_event() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            raise AssertionError("stream path expected")

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            calls.append(self.index)
            yield ResponseCompletedEvent.model_construct(type="response.completed")
            raise httpx.ReadError(
                "temporary",
                request=httpx.Request("POST", "https://llm.example.test"),
            )

    model = _CredentialAwareSdkModel(connection=connection, model_factory=_Model, max_attempts=2)

    async def collect() -> list[Any]:
        return [
            event
            async for event in model.stream_response(
                None, [], ModelSettings(), [], None, [], "disabled"
            )
        ]

    with pytest.raises(httpx.ReadError):
        asyncio.run(collect())
    assert calls == [0]
    assert connection.selected_api_key_index == 0


def test_credential_stream_does_not_retry_after_published_content() -> None:
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    calls: list[int] = []

    class _Model(Model):
        def __init__(self, index: int) -> None:
            self.index = index

        async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
            raise AssertionError("stream path expected")

        async def stream_response(self, *_args: Any, **_kwargs: Any):
            calls.append(self.index)
            yield ResponseTextDeltaEvent.model_construct(
                type="response.output_text.delta",
                delta="published",
            )
            raise httpx.ReadError(
                "temporary",
                request=httpx.Request("POST", "https://llm.example.test"),
            )

    model = _CredentialAwareSdkModel(connection=connection, model_factory=_Model, max_attempts=2)

    async def collect() -> list[str]:
        return [event async for event in model.stream_response(None, [], ModelSettings(), [], None, [], "disabled")]

    with pytest.raises(httpx.ReadError):
        asyncio.run(collect())
    assert calls == [0]
    assert connection.selected_api_key_index == 0


def _service(
    *,
    tool_execution_policy: Any | None = None,
) -> AgentsSdkTurnRuntime[ToolOrchestrationState, dict[str, Any]]:
    final_payload_builder = FinalPayloadBuilderService(
        normalize_answer=lambda *, answer, state: answer,
    )
    return AgentsSdkTurnRuntime(
        logger=logging.getLogger(__name__),
        emit_diagnostic_event=lambda *_args, **_kwargs: None,
        tool_execution_policy=tool_execution_policy or _ToolExecutionPolicy(),
        build_final_payload_from_parsed=(final_payload_builder.build_final_payload_from_parsed),
        llm_request_failed_payload=lambda state, _name, error: {
            "answer": str(error),
            "warnings": list(state.warnings),
        },
    )


def _transport(
    completions: list[str],
    reasoning_blocks: list[str] | None = None,
    text_deltas: list[str] | None = None,
    run_items: list[AgentRuntimeRunItemEvent] | None = None,
) -> AgentRuntimeTransport[ToolOrchestrationState, dict[str, Any]]:
    def prepare_response_content(raw: str, reasoning: str) -> AgentRuntimePreparedContent:
        if reasoning and reasoning_blocks is not None:
            reasoning_blocks.append(reasoning)
        return AgentRuntimePreparedContent(
            raw_content=raw,
            completion_raw_content=raw,
            finalizable_content=raw.strip(),
        )

    return AgentRuntimeTransport(
        before_iteration=lambda _context: None,
        prepare_response_content=prepare_response_content,
        on_llm_failure=lambda _exc, _state: None,
        on_completion=lambda completion, _prepared: completions.append(completion.completion_type),
        adapt_payload=lambda payload: payload,
        on_reasoning_delta=(reasoning_blocks.append if reasoning_blocks is not None else None),
        on_text_delta=text_deltas.append if text_deltas is not None else None,
        on_run_item=run_items.append if run_items is not None else None,
    )


def test_sdk_base_url_preserves_responses_provider_prefix() -> None:
    assert (
        _service()._sdk_base_url("https://llm.example.test/openai/v1/responses")
        == "https://llm.example.test/openai/v1"
    )


def test_sdk_base_url_preserves_chat_completions_provider_prefix() -> None:
    assert (
        _service()._sdk_base_url("https://llm.example.test/openai/v1/chat/completions")
        == "https://llm.example.test/openai/v1"
    )


def test_sdk_base_url_rejects_unsupported_endpoint() -> None:
    with pytest.raises(AgentsSdkCapabilityError):
        _service()._sdk_base_url("https://llm.example.test/v1/generate")


def test_instructions_are_separated_from_conversation_input() -> None:
    instructions, items = _service()._instructions_and_input([
        {"role": "system", "content": "system contract"},
        {"role": "developer", "content": "workspace policy"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ])

    assert instructions == "system contract\n\nworkspace policy"
    assert items == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]


def test_deferred_tools_use_exact_state_driven_enablement() -> None:
    state = ToolOrchestrationState()
    deferred = {
        "type": "function",
        "function": {
            "name": "deferred_lookup",
            "description": "Lookup",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    state.deferred_tool_definitions["deferred_lookup"] = deferred
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="Вопрос"),
        db_connectors=[],
        messages=[{"role": "user", "content": "Вопрос"}],
        tools=[],
        state=state,
        tool_executor=_ToolExecutor(),
    )
    registry = _FunctionToolRegistry(
        coordinator=_service(),
        runtime=runtime,
        transport=_transport([]),
        turn_control=_SdkTurnControl(max_turns=100),
    )
    tool = next(item for item in registry.tools if item.name == "deferred_lookup")

    assert callable(tool.is_enabled)
    assert tool.is_enabled(None, None) is False
    state.loaded_tool_definitions["deferred_lookup"] = deferred
    assert tool.is_enabled(None, None) is True


def test_explicit_final_answer_tool_stays_enabled_during_forced_finalization() -> None:
    turn_control = _SdkTurnControl(max_turns=100)
    turn_control.completed_turns = 98
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="Заверши"),
        db_connectors=[],
        messages=[{"role": "user", "content": "Заверши"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    registry = _FunctionToolRegistry(
        coordinator=_service(),
        runtime=runtime,
        transport=_transport([]),
        turn_control=turn_control,
        explicit_final_answer_enabled=True,
    )
    tools = {tool.name: tool for tool in registry.tools}

    assert tools["lookup"].is_enabled(None, None) is False
    assert tools["submit_final_answer"].is_enabled is True


def test_explicit_final_answer_records_success_without_changing_sdk_output() -> None:
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="Здаров"),
        db_connectors=[],
        messages=[{"role": "user", "content": "Здаров"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    registry = _FunctionToolRegistry(
        coordinator=_service(),
        runtime=runtime,
        transport=_transport([]),
        turn_control=_SdkTurnControl(max_turns=100),
        explicit_final_answer_enabled=True,
    )
    tool = next(item for item in registry.tools if item.name == "submit_final_answer")
    output = asyncio.run(
        tool.on_invoke_tool(
            SimpleNamespace(tool_call_id="call-final"),
            json.dumps({
                "schema_version": "v1",
                "answer_markdown": "Здарова!",
                "uncertainty_flags": [],
                "clarification_request": None,
                "visualization": None,
            }),
        )
    )

    assert ChatFinalResponseContractV1.model_validate_json(output).answer_markdown == "Здарова!"
    assert registry.tool_result("call-final") == {"ok": True, "status": "succeeded"}


def test_invalid_explicit_final_answer_records_validation_failure() -> None:
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="Ответь"),
        db_connectors=[],
        messages=[{"role": "user", "content": "Ответь"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    registry = _FunctionToolRegistry(
        coordinator=_service(),
        runtime=runtime,
        transport=_transport([]),
        turn_control=_SdkTurnControl(max_turns=100),
        explicit_final_answer_enabled=True,
    )
    tool = next(item for item in registry.tools if item.name == "submit_final_answer")
    output = asyncio.run(
        tool.on_invoke_tool(
            SimpleNamespace(tool_call_id="call-invalid"),
            '{"schema_version":"v1","answer_markdown":""}',
        )
    )

    assert json.loads(output)["error_code"] == "llm_final_payload_invalid"
    assert registry.tool_result("call-invalid")["ok"] is False


def test_sdk_runtime_returns_typed_final_output(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(
        _agent: Any,
        *,
        input: Any,  # noqa: A002 - matches Runner.run's public keyword
        max_turns: int,
        hooks: Any,
        run_config: Any,
    ) -> Any:
        _ = input, max_turns, run_config
        await hooks.on_llm_start(None, None, None, [])
        await hooks.on_llm_end(
            None,
            None,
            ModelResponse(
                output=[],
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                ),
                response_id="response-1",
            ),
        )
        return SimpleNamespace(final_output=ChatFinalResponseContractV1(answer_markdown="Готово"))

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    state = ToolOrchestrationState()
    completions: list[str] = []
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Вопрос", llm_connector_name="test"),
                db_connectors=[],
                messages=[
                    {"role": "system", "content": "Answer precisely"},
                    {"role": "user", "content": "Вопрос"},
                ],
                tools=[],
                state=state,
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport(completions),
        )
    )

    assert result["answer"] == "Готово"
    assert completions == ["success"]
    assert len(state.llm_usages) == 1
    assert state.llm_usages[0].total_tokens == 15


def test_sdk_runtime_records_final_answer_before_root_trace_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class FakeLangfuseTurn:
        trace_id = "trace-after-finalization"

        def record_final_output(self, answer_markdown: str) -> None:
            lifecycle.append(f"output:{answer_markdown}")

    @contextmanager
    def fake_langfuse_agent_turn(_request: Any, *, streamed: bool):
        _ = streamed
        lifecycle.append("entered")
        try:
            yield FakeLangfuseTurn()
        finally:
            lifecycle.append("closed")

    async def fake_run(_agent: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(final_output=ChatFinalResponseContractV1(answer_markdown="Готово"))

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.langfuse_agent_turn",
        fake_langfuse_agent_turn,
    )
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )

    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Вопрос", llm_connector_name="test"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Вопрос"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert lifecycle == ["entered", "output:Готово", "closed"]
    assert result["runtime_context"]["langfuse_trace_id"] == "trace-after-finalization"


def test_plain_text_connector_does_not_request_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_output_types: list[Any] = []

    async def fake_run(
        agent: Any,
        *,
        input: Any,  # noqa: A002 - matches Runner.run's public keyword
        max_turns: int,
        hooks: Any,
        run_config: Any,
    ) -> Any:
        _ = input, max_turns, hooks, run_config
        observed_output_types.append(agent.output_type)
        return SimpleNamespace(final_output="Привет!")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="здарова"),
                db_connectors=[],
                messages=[{"role": "user", "content": "здарова"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert observed_output_types == [None]
    assert result["answer"] == "Привет!"


def test_sdk_runtime_passes_client_managed_session_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_sessions: list[Any] = []

    async def fake_run(_agent: Any, **kwargs: Any) -> Any:
        observed_sessions.append(kwargs.get("session"))
        return SimpleNamespace(final_output="Session works")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    agent_session = SimpleNamespace(session_id="conversation-1")
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="remember this"),
                db_connectors=[],
                messages=[{"role": "user", "content": "remember this"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
                agent_session=agent_session,
            ),
            transport=_transport([]),
        )
    )

    assert observed_sessions == [agent_session]
    assert result["answer"] == "Session works"


def test_compaction_restart_atomically_replaces_session_and_uses_empty_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {"role": "user", "content": "Rhetorica context checkpoint v1"}
    runner_inputs: list[Any] = []
    runner_models: list[Model] = []
    replacements: list[list[dict[str, Any]]] = []
    filter_calls = 0

    class FakeSession:
        session_id = "conversation-1"

        async def replace_items(self, items: list[dict[str, Any]]) -> None:
            replacements.append(items)

    class FakeController:
        async def __call__(self, _data: Any) -> Any:
            nonlocal filter_calls
            filter_calls += 1
            if filter_calls == 1:
                raise _SdkCompactionRestart(checkpoint)
            return ModelInputData(input=[], instructions=None)

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime._SdkModelInputController",
        lambda **_kwargs: FakeController(),
    )

    async def fake_run(agent: Any, **kwargs: Any) -> Any:
        runner_inputs.append(kwargs["input"])
        runner_models.append(agent.model)
        await kwargs["run_config"].call_model_input_filter(None)
        return SimpleNamespace(final_output="Done")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        model="test-model",
        api_keys=["key-a", "key-b"],
        extra_settings={"structured_output_enabled": False},
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=SimpleNamespace(connection=connection),
                request=ChatAskRequest(question="continue"),
                db_connectors=[],
                messages=[{"role": "user", "content": "continue"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
                agent_session=FakeSession(),
            ),
            transport=_transport([]),
        )
    )

    assert runner_inputs == [[{"role": "user", "content": "continue"}], []]
    assert replacements == [[checkpoint]]
    assert len(runner_models) == 2
    assert runner_models[0] is runner_models[1]
    assert isinstance(runner_models[0], _CredentialAwareSdkModel)
    assert connection.selected_api_key_index == 0
    assert result["answer"] == "Done"


def test_chat_completions_connector_uses_stock_sdk_model_and_emits_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_models: list[Any] = []
    reasoning_blocks: list[str] = []

    async def fake_run(
        agent: Any,
        *,
        input: Any,  # noqa: A002 - matches Runner.run's public keyword
        max_turns: int,
        hooks: Any,
        run_config: Any,
    ) -> Any:
        _ = input, max_turns, run_config
        observed_models.append(agent.model)
        await hooks.on_llm_start(None, agent, None, [])
        await hooks.on_llm_end(
            None,
            agent,
            ModelResponse(
                output=[
                    ResponseReasoningItem(
                        id="reasoning-1",
                        summary=[Summary(text="Проверяю данные", type="summary_text")],
                        type="reasoning",
                    )
                ],
                usage=Usage(requests=1),
                response_id="response-1",
            ),
        )
        return SimpleNamespace(final_output="Готово")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_ThinkingPlainTextLlmClient(),
                request=ChatAskRequest(question="Вопрос"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Вопрос"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([], reasoning_blocks),
        )
    )

    assert len(observed_models) == 1
    assert isinstance(observed_models[0], OpenAIChatCompletionsModel)
    assert observed_models[0].should_replay_reasoning_content is not None
    assert observed_models[0].should_replay_reasoning_content(SimpleNamespace()) is True
    assert reasoning_blocks == ["Проверяю данные"]
    assert result["answer"] == "Готово"


@pytest.mark.parametrize(
    ("endpoint", "expected_model"),
    [
        ("/responses", OpenAIResponsesModel),
        ("/chat/completions", OpenAIChatCompletionsModel),
    ],
)
def test_pinned_credential_wraps_both_stock_sdk_transports(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    expected_model: type[Model],
) -> None:
    observed_models: list[Model] = []

    async def fake_run(agent: Any, **_kwargs: Any) -> Any:
        observed_models.append(agent.model)
        return SimpleNamespace(final_output="Готово")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_run,
    )
    connection = LlmConnectionConfig(
        base_url="https://llm.example.test/v1",
        endpoint_path=endpoint,
        model="test-model",
        api_keys=["key-a", "key-b"],
        extra_settings={"structured_output_enabled": False},
    )
    connection.configure_credential_binding(
        connector_id="connector-1",
        credential_set_version=1,
        selected_api_key_index=0,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=SimpleNamespace(connection=connection),
                request=ChatAskRequest(question="test"),
                db_connectors=[],
                messages=[{"role": "user", "content": "test"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert result["answer"] == "Готово"
    assert len(observed_models) == 1
    assert isinstance(observed_models[0], _CredentialAwareSdkModel)
    assert isinstance(observed_models[0]._model_factory(0), expected_model)


def test_real_sdk_runner_executes_tool_then_accepts_typed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SequencedModel(Model):
        def __init__(self) -> None:
            self.calls = 0

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args, kwargs
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments='{"value":"evidence"}',
                            call_id="call-1",
                            name="echo_evidence",
                            type="function_call",
                        )
                    ],
                    usage=Usage(
                        requests=1,
                        input_tokens=10,
                        output_tokens=3,
                        total_tokens=13,
                    ),
                    response_id="response-1",
                )
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="message-1",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text=(
                                    '{"schema_version":"v1","answer_markdown":"SDK готов",'
                                    '"uncertainty_flags":[],"clarification_request":null,'
                                    '"visualization":null}'
                                ),
                                type="output_text",
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                usage=Usage(
                    requests=1,
                    input_tokens=14,
                    output_tokens=8,
                    total_tokens=22,
                ),
                response_id="response-2",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    model = _SequencedModel()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.OpenAIResponsesModel",
        lambda **_kwargs: model,
    )
    executor = _RecordingToolExecutor()
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Найди доказательство"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Найди доказательство"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "echo_evidence",
                            "description": "Return exact evidence",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        },
                    }
                ],
                state=ToolOrchestrationState(),
                tool_executor=executor,
            ),
            transport=_transport([]),
        )
    )

    assert model.calls == 2
    assert executor.calls[0]["arguments"] == {"value": "evidence"}
    assert result["answer"] == "SDK готов"


def test_compaction_restarts_sdk_history_before_provider_reuses_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CompactionAwareModel(Model):
        def __init__(self) -> None:
            self.main_calls = 0
            self.compaction_calls = 0

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args
            instructions = str(kwargs.get("system_instructions") or "")
            if "continuation checkpoint" in instructions:
                self.compaction_calls += 1
                return ModelResponse(
                    output=[
                        ResponseOutputMessage(
                            id="compaction-message",
                            content=[
                                ResponseOutputText(
                                    annotations=[],
                                    text="First lookup completed; continue with the next value.",
                                    type="output_text",
                                )
                            ],
                            role="assistant",
                            status="completed",
                            type="message",
                        )
                    ],
                    usage=Usage(requests=1),
                    response_id="compaction-response",
                )

            self.main_calls += 1
            if self.main_calls <= 2:
                value = "first" if self.main_calls == 1 else "second"
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments=json.dumps({"value": value}),
                            call_id="provider-reused-call-id",
                            name="echo_evidence",
                            type="function_call",
                        )
                    ],
                    usage=Usage(requests=1),
                    response_id=f"response-{self.main_calls}",
                )
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id="final-message",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text=(
                                    '{"schema_version":"v1","answer_markdown":"Оба вызова готовы",'
                                    '"uncertainty_flags":[],"clarification_request":null,'
                                    '"visualization":null}'
                                ),
                                type="output_text",
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                usage=Usage(requests=1),
                response_id="response-final",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    class _CompactionTriggeringExecutor(_RecordingToolExecutor):
        def execute_tool(self, **kwargs: Any) -> dict[str, Any]:
            result = super().execute_tool(**kwargs)
            if len(self.calls) == 1:
                kwargs["state"].force_context_compaction = True
            return result

    model = _CompactionAwareModel()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.OpenAIResponsesModel",
        lambda **_kwargs: model,
    )
    executor = _CompactionTriggeringExecutor()
    state = ToolOrchestrationState()
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Сделай два вызова"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Сделай два вызова"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "echo_evidence",
                            "description": "Return evidence",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        },
                    }
                ],
                state=state,
                tool_executor=executor,
            ),
            transport=_transport([]),
        )
    )

    assert [call["arguments"]["value"] for call in executor.calls] == ["first", "second"]
    assert model.compaction_calls == 1
    assert model.main_calls == 3
    assert result["answer"] == "Оба вызова готовы"
    assert "context_compacted" in state.warnings


def test_explicit_final_answer_uses_native_sdk_tool_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExplicitFinalModel(Model):
        def __init__(self) -> None:
            self.calls = 0
            self.tool_choices: list[Any] = []

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args
            self.calls += 1
            self.tool_choices.append(kwargs["model_settings"].tool_choice)
            if self.calls == 1:
                return ModelResponse(
                    output=[
                        ResponseFunctionToolCall(
                            arguments='{"value":"evidence"}',
                            call_id="call-domain",
                            name="echo_evidence",
                            type="function_call",
                        )
                    ],
                    usage=Usage(requests=1),
                    response_id="response-domain",
                )
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        arguments=(
                            '{"schema_version":"v1","answer_markdown":"Найдено",'
                            '"uncertainty_flags":[],"clarification_request":null,'
                            '"visualization":null}'
                        ),
                        call_id="call-final",
                        name="submit_final_answer",
                        type="function_call",
                    )
                ],
                usage=Usage(requests=1),
                response_id="response-final",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    model = _ExplicitFinalModel()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.OpenAIResponsesModel",
        lambda **_kwargs: model,
    )
    connection = _LlmClient.connection.model_copy(
        update={
            "extra_settings": {
                "context_window_tokens": 200_000,
                "require_explicit_final_answer": True,
            }
        }
    )
    executor = _RecordingToolExecutor()
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=SimpleNamespace(connection=connection),
                request=ChatAskRequest(question="Найди"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Найди"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "echo_evidence",
                            "description": "Return evidence",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        },
                    }
                ],
                state=ToolOrchestrationState(),
                tool_executor=executor,
            ),
            transport=_transport([]),
        )
    )

    assert model.calls == 2
    assert model.tool_choices == ["required", "required"]
    assert executor.calls[0]["arguments"] == {"value": "evidence"}
    assert result["answer"] == "Найдено"


def test_invalid_explicit_final_answer_is_returned_to_model_for_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RepairingFinalModel(Model):
        def __init__(self) -> None:
            self.calls = 0

        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args, kwargs
            self.calls += 1
            answer = "" if self.calls == 1 else "Исправлено"
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        arguments=json.dumps({
                            "schema_version": "v1",
                            "answer_markdown": answer,
                            "uncertainty_flags": [],
                            "clarification_request": None,
                            "visualization": None,
                        }),
                        call_id=f"call-final-{self.calls}",
                        name="submit_final_answer",
                        type="function_call",
                    )
                ],
                usage=Usage(requests=1),
                response_id=f"response-final-{self.calls}",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    model = _RepairingFinalModel()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.OpenAIResponsesModel",
        lambda **_kwargs: model,
    )
    connection = _LlmClient.connection.model_copy(
        update={"extra_settings": {"require_explicit_final_answer": True}}
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=SimpleNamespace(connection=connection),
                request=ChatAskRequest(question="Ответь"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Ответь"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert model.calls == 2
    assert result["answer"] == "Исправлено"


def test_valid_explicit_final_answer_survives_malformed_sibling_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FinalWithMalformedCleanupModel(Model):
        async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
            _ = args, kwargs
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        arguments=(
                            '{"schema_version":"v1","answer_markdown":"Найдено",'
                            '"uncertainty_flags":[],"clarification_request":null,'
                            '"visualization":null}'
                        ),
                        call_id="call-final",
                        name="submit_final_answer",
                        type="function_call",
                    ),
                    ResponseFunctionToolCall(
                        arguments='{"group_name":"mcp_connectors"',
                        call_id="call-cleanup",
                        name="unload_tool_group",
                        type="function_call",
                    ),
                ],
                usage=Usage(requests=1),
                response_id="response-final",
            )

        async def stream_response(self, *args: Any, **kwargs: Any):
            _ = args, kwargs
            if False:
                yield None

    model = _FinalWithMalformedCleanupModel()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.OpenAIResponsesModel",
        lambda **_kwargs: model,
    )
    connection = _LlmClient.connection.model_copy(
        update={"extra_settings": {"require_explicit_final_answer": True}}
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=SimpleNamespace(connection=connection),
                request=ChatAskRequest(question="Найди"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Найди"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "unload_tool_group",
                            "description": "Unload a tool group",
                            "parameters": {
                                "type": "object",
                                "properties": {"group_name": {"type": "string"}},
                                "required": ["group_name"],
                            },
                        },
                    }
                ],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert result["answer"] == "Найдено"
    assert "agent_runtime_model_behavior_error" not in result["warnings"]


def test_streaming_runtime_uses_sdk_stream_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    reasoning_blocks: list[str] = []

    class _StreamedResult:
        final_output = ChatFinalResponseContractV1(answer_markdown="Поток готов")

        def __init__(self, hooks: Any, agent: Any) -> None:
            self._hooks = hooks
            self._agent = agent

        async def stream_events(self):
            calls.append("stream")
            await self._hooks.on_llm_start(None, self._agent, None, [])
            await self._hooks.on_llm_end(
                None,
                self._agent,
                ModelResponse(
                    output=[
                        ResponseReasoningItem(
                            id="reasoning-stream-1",
                            summary=[Summary(text="Потоковый анализ", type="summary_text")],
                            type="reasoning",
                        )
                    ],
                    usage=Usage(requests=1),
                    response_id="response-stream-1",
                ),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=ResponseReasoningSummaryTextDeltaEvent(
                    delta="Потоковый анализ",
                    item_id="reasoning-stream-1",
                    output_index=0,
                    summary_index=0,
                    sequence_number=1,
                    type="response.reasoning_summary_text.delta",
                ),
            )

    def fake_run_streamed(agent: Any, *_args: Any, **kwargs: Any) -> _StreamedResult:
        return _StreamedResult(kwargs["hooks"], agent)

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run_streamed",
        fake_run_streamed,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Вопрос"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Вопрос"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
                streamed=True,
            ),
            transport=_transport([], reasoning_blocks),
        )
    )

    assert calls == ["stream"]
    assert reasoning_blocks == ["Потоковый анализ"]
    assert result["answer"] == "Поток готов"


def test_streaming_compaction_restarts_sdk_from_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_inputs: list[list[dict[str, Any]]] = []

    async def fake_compaction_run(agent: Any, **_kwargs: Any) -> Any:
        assert agent.name == "Rhetorica context compactor"
        return SimpleNamespace(final_output="Continue from the compact checkpoint.")

    class _StreamedResult:
        is_complete = False

        def __init__(self, agent: Any, input_items: list[dict[str, Any]], kwargs: dict[str, Any]):
            self.final_output = ChatFinalResponseContractV1(
                answer_markdown="После compaction готово"
            )
            self._agent = agent
            self._input = input_items
            self._kwargs = kwargs

        def cancel(self, mode: str = "immediate") -> None:
            _ = mode
            self.is_complete = True

        async def stream_events(self):
            run_config = self._kwargs["run_config"]
            filtered = await run_config.call_model_input_filter(
                CallModelData(
                    model_data=ModelInputData(
                        input=self._input,
                        instructions="system contract",
                    ),
                    agent=self._agent,
                    context=None,
                )
            )
            hooks = self._kwargs["hooks"]
            await hooks.on_llm_start(
                None,
                self._agent,
                filtered.instructions,
                filtered.input,
            )
            await hooks.on_llm_end(
                None,
                self._agent,
                ModelResponse(
                    output=[],
                    usage=Usage(requests=1),
                    response_id="response-after-compaction",
                ),
            )
            self.is_complete = True
            if False:
                yield None

    def fake_run_streamed(
        agent: Any,
        *,
        input: list[dict[str, Any]],  # noqa: A002 - SDK public keyword
        **kwargs: Any,
    ) -> _StreamedResult:
        runner_inputs.append(input)
        return _StreamedResult(agent, input, kwargs)

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_compaction_run,
    )
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run_streamed",
        fake_run_streamed,
    )
    state = ToolOrchestrationState(force_context_compaction=True)
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Продолжай"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Продолжай"}],
                tools=[],
                state=state,
                tool_executor=_ToolExecutor(),
                streamed=True,
            ),
            transport=_transport([]),
        )
    )

    assert len(runner_inputs) == 2
    assert runner_inputs[0] == [{"role": "user", "content": "Продолжай"}]
    assert runner_inputs[1][0]["content"].startswith("Rhetorica context checkpoint v1")
    assert result["answer"] == "После compaction готово"


def test_streaming_plain_text_connector_returns_markdown_without_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_output_types: list[Any] = []
    text_deltas: list[str] = []

    class _StreamedResult:
        final_output = "Здарова!"

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=ResponseTextDeltaEvent.model_construct(delta="Здарова!"),
            )

    def fake_run_streamed(agent: Any, *_args: Any, **_kwargs: Any) -> _StreamedResult:
        observed_output_types.append(agent.output_type)
        return _StreamedResult()

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run_streamed",
        fake_run_streamed,
    )
    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="здарова"),
                db_connectors=[],
                messages=[{"role": "user", "content": "здарова"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
                streamed=True,
            ),
            transport=_transport([], text_deltas=text_deltas),
        )
    )

    assert observed_output_types == [None]
    assert text_deltas == ["Здарова!"]
    assert result["answer"] == "Здарова!"


def test_explicit_cancel_uses_native_streaming_result_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_modes: list[str] = []

    class _StreamedResult:
        final_output = "must not be used"
        is_complete = False

        def __init__(self) -> None:
            self._finished = asyncio.Event()

        def cancel(self, mode: str = "immediate") -> None:
            cancel_modes.append(mode)
            self.is_complete = True
            self._finished.set()

        async def stream_events(self):
            await self._finished.wait()
            if False:
                yield None

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run_streamed",
        lambda *_args, **_kwargs: _StreamedResult(),
    )
    cancel_event = asyncio.Event()
    cancel_event.set()
    transport = _transport([])
    transport.on_cancelled = lambda: {"answer": "cancelled", "warnings": ["cancelled"]}

    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="cancel"),
                db_connectors=[],
                messages=[{"role": "user", "content": "cancel"}],
                tools=[],
                state=ToolOrchestrationState(),
                tool_executor=_ToolExecutor(),
                streamed=True,
                cancel_event=cancel_event,
            ),
            transport=transport,
        )
    )

    assert cancel_modes == ["immediate"]
    assert result["answer"] == "cancelled"


def test_streaming_runtime_preserves_backend_owned_visualization_and_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StreamedResult:
        final_output = "График и файл готовы"

        async def stream_events(self):
            if False:
                yield None

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run_streamed",
        lambda *_args, **_kwargs: _StreamedResult(),
    )
    visualization = VisualizationSpec.model_validate({
        "version": "v2",
        "kind": "line",
        "title": "Net Revenue и GMV",
        "planner_mode": "llm_validated",
        "data_source": {
            "execution_id": "exec-1",
            "row_count": 7,
            "is_truncated": False,
        },
        "encoding": {"x": "dt", "y": ["net_revenue_mln", "gmv_mln"]},
        "rationale": {
            "why_kind": "Динамика по дням",
            "why_fields": ["dt", "net_revenue_mln", "gmv_mln"],
        },
    })
    published_file = FileArtifactRef(
        id="artifact-1",
        filename="net_revenue_gmv.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=6800,
        kind="sandbox_output",
        sha256="0" * 64,
    )
    state = ToolOrchestrationState(
        visualization_candidate=visualization,
        visualization_candidates=[visualization],
        published_files=[published_file],
    )

    result = asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="Покажи график и файл"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Покажи график и файл"}],
                tools=[],
                state=state,
                tool_executor=_ToolExecutor(),
                streamed=True,
            ),
            transport=_transport([]),
        )
    )

    assert result["answer"] == "График и файл готовы"
    assert result["visualization"]["data_source"]["execution_id"] == "exec-1"
    assert result["files"] == [published_file.model_dump(mode="json")]
    assert result["analysis_document"]["blocks"][0]["kind"] == "chart"


def test_chat_completions_model_settings_honor_max_completion_tokens() -> None:
    connection = _LlmClient.connection.model_copy(
        update={
            "endpoint_path": "/chat/completions",
            "max_tokens": 777,
            "max_tokens_parameter": "max_completion_tokens",
        }
    )

    settings = _service()._model_settings(connection)

    assert settings.max_tokens is None
    assert settings.extra_args == {"max_completion_tokens": 777}


def test_responses_model_settings_use_client_compaction_and_reasoning_summary() -> None:
    connection = _LlmClient.connection.model_copy(
        update={
            "endpoint_path": "/responses",
            "max_tokens": 30_000,
            "extra_settings": {
                "context_window_tokens": 200_000,
                "chat_template_enable_thinking": True,
            },
        }
    )

    settings = _service()._model_settings(connection)

    assert settings.context_management is None
    assert settings.reasoning is not None
    assert settings.reasoning.summary == "auto"


def test_chat_completions_model_settings_do_not_send_responses_compaction() -> None:
    connection = _LlmClient.connection.model_copy(
        update={
            "endpoint_path": "/chat/completions",
            "extra_settings": {"context_window_tokens": 200_000},
        }
    )

    settings = _service()._model_settings(connection)

    assert settings.context_management is None
    assert settings.reasoning is None


@pytest.mark.parametrize("endpoint_path", ["/responses", "/chat/completions"])
def test_model_settings_forward_reasoning_effort_for_any_sdk_endpoint(endpoint_path: str) -> None:
    connection = _LlmClient.connection.model_copy(
        update={"endpoint_path": endpoint_path, "reasoning_effort": "high"}
    )

    settings = _service()._model_settings(connection)

    assert settings.reasoning is not None
    assert settings.reasoning.effort == "high"
    assert settings.reasoning.summary is None


def test_model_settings_preserve_responses_summary_with_effort() -> None:
    connection = _LlmClient.connection.model_copy(
        update={
            "endpoint_path": "/responses",
            "reasoning_effort": "low",
            "extra_settings": {"chat_template_enable_thinking": True},
        }
    )

    settings = _service()._model_settings(connection)

    assert settings.reasoning is not None
    assert settings.reasoning.effort == "low"
    assert settings.reasoning.summary == "auto"


def test_tool_error_is_returned_to_model_without_terminal_backend_stop() -> None:
    class _ErrorExecutor:
        def execute_tool(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": False,
                "can_continue": False,
                "error_code": "upstream_failed",
                "user_message": "Upstream failed.",
            }

    definition = {
        "type": "function",
        "function": {
            "name": "failing_tool",
            "description": "Fail deterministically",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    state = ToolOrchestrationState()
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="fail"),
        db_connectors=[],
        messages=[{"role": "user", "content": "fail"}],
        tools=[definition],
        state=state,
        tool_executor=_ErrorExecutor(),
    )
    registry = _FunctionToolRegistry(
        coordinator=_service(tool_execution_policy=ToolExecutionPolicy()),
        runtime=runtime,
        transport=_transport([]),
        turn_control=_SdkTurnControl(max_turns=100),
    )

    result = asyncio.run(
        registry._invoke(
            SimpleNamespace(
                tool_name="failing_tool",
                tool_call_id="call-fail",
                agent=None,
            ),
            "{}",
        )
    )

    assert "upstream_failed" in result
    assert state.tool_call_count == 1


def test_fourth_identical_call_requests_forced_compaction() -> None:
    definition = {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run SQL",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    }
    state = ToolOrchestrationState()
    for call_id in ("call-1", "call-2"):
        state.record_executed_tool_call(
            tool_call_id=call_id,
            tool_name="run_sql",
            arguments={"sql": "SELECT 1"},
            result={"ok": True},
            prompt_result={},
        )
    executor = _RecordingToolExecutor()
    registry = _FunctionToolRegistry(
        coordinator=_service(tool_execution_policy=ToolExecutionPolicy()),
        runtime=AgentRuntimeInput(
            llm_client=_LlmClient(),
            request=ChatAskRequest(question="repeat"),
            db_connectors=[],
            messages=[{"role": "user", "content": "repeat"}],
            tools=[definition],
            state=state,
            tool_executor=executor,
        ),
        transport=_transport([]),
        turn_control=_SdkTurnControl(max_turns=100),
    )

    async def repeat() -> tuple[str, str]:
        registry.begin_model_turn(2)
        third = await registry._invoke(
            SimpleNamespace(tool_name="run_sql", tool_call_id="call-3", agent=None),
            '{"sql":"SELECT 1"}',
        )
        assert state.blocked_tool_call_turn_count == 1
        assert state.force_context_compaction is False
        registry.begin_model_turn(3)
        fourth = await registry._invoke(
            SimpleNamespace(tool_name="run_sql", tool_call_id="call-4", agent=None),
            '{"sql":"SELECT 1"}',
        )
        return third, fourth

    third, fourth = asyncio.run(repeat())

    assert "identical_tool_call_limit_reached" in third
    assert "identical_tool_call_limit_reached" in fourth
    assert state.blocked_tool_call_turn_count == 2
    assert state.force_context_compaction is True
    assert executor.calls == []


def test_forced_compaction_replaces_sdk_input_with_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_compaction_run(*_args: Any, **kwargs: Any) -> Any:
        assert kwargs["max_turns"] == 1
        assert kwargs["run_config"].call_model_input_filter is None
        return SimpleNamespace(final_output="The two successful SELECT 1 calls are enough.")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_compaction_run,
    )
    state = ToolOrchestrationState(
        blocked_tool_call_turn_count=2,
        force_context_compaction=True,
    )
    state.record_executed_tool_call(
        tool_call_id="call-1",
        tool_name="run_sql",
        arguments={"sql": "SELECT 1"},
        result={"ok": True},
        prompt_result={},
    )
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="repeat"),
        db_connectors=[],
        messages=[{"role": "user", "content": "repeat"}],
        tools=[],
        state=state,
        tool_executor=_ToolExecutor(),
    )
    turn_control = _SdkTurnControl(max_turns=100)
    controller = _SdkModelInputController(
        runtime=runtime,
        model=OpenAIChatCompletionsModel(
            model="test-model",
            openai_client=SimpleNamespace(),
        ),  # Runner is replaced above.
        model_settings=ModelSettings(max_tokens=256),
        turn_control=turn_control,
        run_config=RunConfig(tracing_disabled=True),
    )

    with pytest.raises(_SdkCompactionRestart) as restart:
        asyncio.run(
            controller(
                CallModelData(
                    model_data=ModelInputData(
                        input=[
                            {"role": "user", "content": "repeat"},
                            {"type": "function_call", "call_id": "call-4"},
                            {"type": "function_call_output", "call_id": "call-4"},
                        ],
                        instructions="system contract",
                    ),
                    agent=SimpleNamespace(),
                    context=None,
                )
            )
        )

    checkpoint = restart.value.checkpoint["content"]
    assert "Rhetorica context checkpoint v1" in checkpoint
    assert "executed_tool_calls" in checkpoint
    assert "SELECT 1" in checkpoint
    assert state.blocked_tool_call_turn_count == 0
    assert state.force_context_compaction is False
    assert "context_compacted" in state.warnings


def test_prompt_budget_threshold_compacts_sdk_input_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compaction_inputs: list[list[dict[str, Any]]] = []

    async def fake_compaction_run(*_args: Any, **kwargs: Any) -> Any:
        compaction_inputs.append(kwargs["input"])
        hooks = kwargs["hooks"]
        await hooks.on_llm_start(None, None, None, kwargs["input"])
        await hooks.on_llm_end(
            None,
            None,
            ModelResponse(
                output=[],
                usage=Usage(requests=1, input_tokens=100, output_tokens=10, total_tokens=110),
                response_id="compaction-response",
            ),
        )
        return SimpleNamespace(final_output="Preserve the current analytics request.")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        fake_compaction_run,
    )
    connection = _LlmClient.connection.model_copy(
        update={
            "max_tokens": 256,
            "extra_settings": {"context_window_tokens": 2_000},
        }
    )
    runtime = AgentRuntimeInput(
        llm_client=SimpleNamespace(connection=connection),
        request=ChatAskRequest(question="analyze"),
        db_connectors=[],
        messages=[{"role": "user", "content": "analyze"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=OpenAIChatCompletionsModel(
            model="test-model",
            openai_client=SimpleNamespace(),
        ),
        model_settings=ModelSettings(max_tokens=256),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
        active_tool_definitions=lambda: [
            {
                "type": "function",
                "function": {
                    "name": "run_sql",
                    "description": "x" * 1_000,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    with pytest.raises(_SdkCompactionRestart) as restart:
        asyncio.run(
            controller(
                CallModelData(
                    model_data=ModelInputData(
                        input=[{"role": "user", "content": "x" * 3_000}],
                        instructions="system contract",
                    ),
                    agent=SimpleNamespace(),
                    context=None,
                )
            )
        )

    assert len(compaction_inputs) == 1
    assert "Rhetorica context checkpoint v1" in restart.value.checkpoint["content"]
    assert runtime.state.force_context_compaction is False
    assert "context_compacted" in runtime.state.warnings
    assert runtime.state.llm_usages[0].phase == "context_compaction"


def test_prompt_budget_threshold_does_not_compact_small_sdk_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_compaction(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("small input must not be compacted")

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime.Runner.run",
        unexpected_compaction,
    )
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="short"),
        db_connectors=[],
        messages=[{"role": "user", "content": "short"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=SimpleNamespace(),
        model_settings=ModelSettings(),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
    )

    prepared = asyncio.run(
        controller(
            CallModelData(
                model_data=ModelInputData(
                    input=[{"role": "user", "content": "short"}],
                    instructions="system contract",
                ),
                agent=SimpleNamespace(),
                context=None,
            )
        )
    )

    assert prepared.input[0] == {"role": "user", "content": "short"}
    assert prepared.input[-1]["role"] == "developer"
    assert "Current date/time context" in prepared.input[-1]["content"]
    assert "not a new task" in prepared.input[-1]["content"]
    assert prepared.instructions == "system contract"
    assert "context_compacted" not in runtime.state.warnings


def test_chat_completions_puts_runtime_context_in_supported_user_role() -> None:
    llm_client = SimpleNamespace(
        connection=_LlmClient.connection.model_copy(update={"endpoint_path": "/chat/completions"})
    )
    runtime = AgentRuntimeInput(
        llm_client=llm_client,
        request=ChatAskRequest(question="short"),
        db_connectors=[],
        messages=[{"role": "user", "content": "short"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=SimpleNamespace(),
        model_settings=ModelSettings(),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
    )

    prepared = asyncio.run(
        controller(
            CallModelData(
                model_data=ModelInputData(
                    input=[{"role": "user", "content": "short"}],
                    instructions="system contract",
                ),
                agent=SimpleNamespace(),
                context=None,
            )
        )
    )

    assert prepared.input[-1]["role"] == "user"
    assert "<runtime_context>" in prepared.input[-1]["content"]
    assert prepared.instructions == "system contract"


@pytest.mark.parametrize(
    ("endpoint_path", "extra_settings", "expected_role"),
    [
        ("/responses", {}, "developer"),
        ("/responses", {"runtime_context_role": "user"}, "user"),
        ("/chat/completions", {}, "user"),
        ("/chat/completions", {"runtime_context_role": "developer"}, "developer"),
    ],
)
def test_runtime_context_role_preserves_trailing_input_and_instructions(
    endpoint_path: str,
    extra_settings: dict[str, str],
    expected_role: str,
) -> None:
    connection = _LlmClient.connection.model_copy(
        update={"endpoint_path": endpoint_path, "extra_settings": extra_settings}
    )
    runtime = AgentRuntimeInput(
        llm_client=SimpleNamespace(connection=connection),
        request=ChatAskRequest(question="short"),
        db_connectors=[],
        messages=[{"role": "user", "content": "short"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=SimpleNamespace(),
        model_settings=ModelSettings(),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
    )

    prepared = asyncio.run(
        controller(
            CallModelData(
                model_data=ModelInputData(
                    input=[{"role": "user", "content": "short"}],
                    instructions="stable system contract",
                ),
                agent=SimpleNamespace(),
                context=None,
            )
        )
    )

    assert prepared.input[0] == {"role": "user", "content": "short"}
    assert prepared.input[-1]["role"] == expected_role
    assert "<runtime_context>" in prepared.input[-1]["content"]
    assert prepared.instructions == "stable system contract"


def test_responses_user_runtime_context_stays_after_completed_tool_loop() -> None:
    connection = _LlmClient.connection.model_copy(
        update={
            "endpoint_path": "/responses",
            "extra_settings": {"runtime_context_role": "user"},
        }
    )
    runtime = AgentRuntimeInput(
        llm_client=SimpleNamespace(connection=connection),
        request=ChatAskRequest(question="analyze"),
        db_connectors=[],
        messages=[{"role": "user", "content": "analyze"}],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=SimpleNamespace(),
        model_settings=ModelSettings(),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
    )
    tool_loop_input = [
        {"role": "user", "content": "analyze"},
        {
            "type": "function_call",
            "id": "fc-1",
            "call_id": "call-1",
            "name": "run_sql",
            "arguments": '{"sql":"SELECT 1"}',
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"ok":true,"rows":[[1]]}',
        },
    ]

    prepared = asyncio.run(
        controller(
            CallModelData(
                model_data=ModelInputData(
                    input=tool_loop_input,
                    instructions="stable system contract",
                ),
                agent=SimpleNamespace(),
                context=None,
            )
        )
    )

    assert prepared.input[:-1] == tool_loop_input
    assert prepared.input[-1]["role"] == "user"
    assert "<runtime_context>" in prepared.input[-1]["content"]
    assert prepared.instructions == "stable system contract"


def test_model_sees_turn_budget_and_final_turns_disable_tools() -> None:
    state = ToolOrchestrationState()
    runtime = AgentRuntimeInput(
        llm_client=_LlmClient(),
        request=ChatAskRequest(question="finish"),
        db_connectors=[],
        messages=[{"role": "user", "content": "finish"}],
        tools=[],
        state=state,
        tool_executor=_ToolExecutor(),
    )
    turn_control = _SdkTurnControl(max_turns=100)
    turn_control.completed_turns = 98
    controller = _SdkModelInputController(
        runtime=runtime,
        model=SimpleNamespace(),
        model_settings=ModelSettings(),
        turn_control=turn_control,
        run_config=RunConfig(tracing_disabled=True),
    )

    prepared = asyncio.run(
        controller(
            CallModelData(
                model_data=ModelInputData(
                    input=[{"role": "user", "content": "finish"}],
                    instructions="system contract",
                ),
                agent=SimpleNamespace(),
                context=None,
            )
        )
    )

    assert turn_control.tools_enabled is False
    assert "forced finalization attempt 1/2" in prepared.input[-1]["content"]
    assert prepared.instructions == "system contract"
    assert "llm_forced_finalization_requested" in state.warnings


def test_max_turns_is_explicit_runtime_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()

    async def fail_run(**_kwargs: Any) -> Any:
        raise MaxTurnsExceeded("limit")

    monkeypatch.setattr(service, "_run_sdk", fail_run)
    state = ToolOrchestrationState()
    result = asyncio.run(
        service.run(
            runtime=AgentRuntimeInput(
                llm_client=_LlmClient(),
                request=ChatAskRequest(question="Вопрос", llm_connector_name="test"),
                db_connectors=[],
                messages=[{"role": "user", "content": "Вопрос"}],
                tools=[],
                state=state,
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )

    assert "agent_runtime_turn_limit_reached" in result["warnings"]
    assert "Agents SDK stopped" in result["answer"]


class _ObservedStreamModel(Model):
    """Exercise the real Runner, including native model timeout/cancel handling."""

    def __init__(self, outcome: str, cancel_event: asyncio.Event | None = None) -> None:
        self.outcome = outcome
        self.cancel_event = cancel_event

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        if self.outcome == "timeout":
            await asyncio.Event().wait()
        if self.outcome == "error":
            raise RuntimeError("provider failure")
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="message-1",
                    role="assistant",
                    status="completed",
                    type="message",
                    content=[ResponseOutputText(text="Done", type="output_text", annotations=[])],
                )
            ],
            usage=Usage(requests=1, input_tokens=5, output_tokens=2, total_tokens=7),
            response_id="response-observed",
            request_id="request-provider-1",
        )

    async def stream_response(self, *args: Any, **kwargs: Any):
        from openai.types.responses import (
            Response,
            ResponseCompletedEvent,
            ResponseCreatedEvent,
            ResponseFunctionCallArgumentsDeltaEvent,
            ResponseUsage,
        )

        response = Response.model_construct(
            id="response-observed",
            object="response",
            model="test-model",
            output=[],
            status="in_progress",
            usage=None,
        )
        yield ResponseCreatedEvent.model_construct(response=response, type="response.created")
        if self.outcome == "silent_timeout":
            await asyncio.Event().wait()
        yield ResponseReasoningSummaryTextDeltaEvent.model_construct(
            type="response.reasoning_summary_text.delta", delta="private reasoning"
        )
        yield ResponseTextDeltaEvent.model_construct(
            type="response.output_text.delta", delta="private text"
        )
        yield ResponseFunctionCallArgumentsDeltaEvent.model_construct(
            type="response.function_call_arguments.delta", delta='{"private":1}'
        )
        if self.outcome == "cancelled":
            assert self.cancel_event is not None
            await asyncio.sleep(0.01)
            self.cancel_event.set()
            await asyncio.Event().wait()
        if self.outcome == "timeout":
            while True:
                await asyncio.sleep(0.005)
                yield ResponseTextDeltaEvent.model_construct(
                    type="response.output_text.delta", delta="x"
                )
        if self.outcome == "error":
            raise RuntimeError("provider failure")
        final = await self.get_response()
        response.output = final.output
        response.status = "completed"
        response.usage = ResponseUsage.model_validate({
            "input_tokens": 5,
            "output_tokens": 2,
            "total_tokens": 7,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
        })
        yield ResponseCompletedEvent.model_construct(response=response, type="response.completed")


@pytest.mark.parametrize(
    "outcome", ["succeeded", "timeout", "silent_timeout", "error", "cancelled"]
)
def test_native_sdk_stream_call_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    cancel_event = asyncio.Event()
    model = _ObservedStreamModel(outcome, cancel_event)
    monkeypatch.setattr(AgentsSdkTurnRuntime, "_sdk_model", staticmethod(lambda **_: model))
    original_settings = AgentsSdkTurnRuntime._model_settings
    monkeypatch.setattr(
        AgentsSdkTurnRuntime,
        "_model_settings",
        staticmethod(
            lambda connection: original_settings(connection).resolve(ModelSettings(timeout=0.08))
        ),
    )
    state = ToolOrchestrationState()
    transport = _transport([])
    transport.on_cancelled = lambda: {"answer": "cancelled"}
    with caplog.at_level(logging.INFO):
        asyncio.run(
            _service().run(
                runtime=AgentRuntimeInput(
                    llm_client=_PlainTextLlmClient(),
                    request=ChatAskRequest(
                        question="test", conversation_id="conversation-observed"
                    ),
                    db_connectors=[],
                    messages=[{"role": "user", "content": "test"}],
                    tools=[],
                    state=state,
                    tool_executor=_ToolExecutor(),
                    streamed=True,
                    cancel_event=cancel_event,
                ),
                transport=transport,
            )
        )
    assert len(state.llm_usages) == 1
    usage = state.llm_usages[0]
    d = usage.diagnostics
    assert d is not None
    assert d.status == ("timeout" if outcome == "silent_timeout" else outcome)
    assert d.call_index == 1
    assert d.started_at <= d.ended_at
    assert usage.duration_ms > 0
    assert usage.usage_reported is (outcome == "succeeded")
    assert usage.total_tokens == (7 if outcome == "succeeded" else 0)
    if outcome == "silent_timeout":
        assert d.first_chunk_at is d.last_chunk_at is None
        assert d.content_event_count == d.text_chars == d.reasoning_chars == 0
    else:
        assert d.first_chunk_at <= d.last_chunk_at
        assert d.text_chars >= len("private text")
        assert d.reasoning_chars == len("private reasoning")
        assert d.tool_arguments_chars == len('{"private":1}')
    assert d.terminal_event == ("response.completed" if outcome == "succeeded" else None)
    records = [r for r in caplog.records if r.msg == "llm.call.completed"]
    assert len(records) == 1
    assert records[0].conversation_id == "conversation-observed"
    assert records[0].status == d.status
    assert records[0].text_chars == d.text_chars
    assert "private text" not in str(records[0].__dict__)
    assert "private reasoning" not in str(records[0].__dict__)


@pytest.mark.parametrize("outcome", ["succeeded", "timeout", "error"])
def test_native_sdk_nonstream_call_diagnostics(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    model = _ObservedStreamModel(outcome)
    monkeypatch.setattr(AgentsSdkTurnRuntime, "_sdk_model", staticmethod(lambda **_: model))
    original_settings = AgentsSdkTurnRuntime._model_settings
    monkeypatch.setattr(
        AgentsSdkTurnRuntime,
        "_model_settings",
        staticmethod(
            lambda connection: original_settings(connection).resolve(ModelSettings(timeout=0.05))
        ),
    )
    state = ToolOrchestrationState()
    asyncio.run(
        _service().run(
            runtime=AgentRuntimeInput(
                llm_client=_PlainTextLlmClient(),
                request=ChatAskRequest(question="test"),
                db_connectors=[],
                messages=[{"role": "user", "content": "test"}],
                tools=[],
                state=state,
                tool_executor=_ToolExecutor(),
            ),
            transport=_transport([]),
        )
    )
    usage = state.llm_usages[0]
    assert usage.diagnostics.status == outcome
    assert usage.diagnostics.streamed is False
    assert usage.diagnostics.first_chunk_at is None
    assert usage.duration_ms > 0
    assert usage.usage_reported is (outcome == "succeeded")
    if outcome == "succeeded":
        assert usage.diagnostics.text_chars == 4
        assert usage.diagnostics.provider_request_id == "request-provider-1"


def test_observer_keeps_stream_calls_separate_when_hooks_run_ahead(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openai.types.responses import Response, ResponseCompletedEvent
    from rhetorica_backend.domain.chat.orchestration.response_assembly import ChatResponseAssembler
    from rhetorica_backend.domain.chat.orchestration.sdk_model_observer import SdkModelCallObserver
    from rhetorica_backend.domain.chat.token_usage import ConversationTokenUsage

    state = ToolOrchestrationState()
    runtime = AgentRuntimeInput(
        llm_client=_PlainTextLlmClient(),
        request=ChatAskRequest(question="test"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=state,
        tool_executor=_ToolExecutor(),
    )
    observer = SdkModelCallObserver(runtime, logging.getLogger(__name__))
    response = ModelResponse(
        output=[],
        usage=Usage(requests=1, total_tokens=7, input_tokens=5, output_tokens=2),
        response_id="r",
    )
    with caplog.at_level(logging.INFO):
        first = observer.start(phase="tool_loop", streamed=True)
        observer.complete(first, response)
        compaction = observer.start(phase="context_compaction", streamed=False)
        observer.complete(compaction, response)
        last = observer.start(phase="tool_loop", streamed=True)
        observer.complete(last, response)
        for text in ["first", "last answer"]:
            observer.observe(
                SimpleNamespace(
                    type="raw_response_event",
                    data=ResponseTextDeltaEvent.model_construct(
                        type="response.output_text.delta", delta=text
                    ),
                )
            )
            observer.observe(
                SimpleNamespace(
                    type="raw_response_event",
                    data=ResponseCompletedEvent.model_construct(
                        response=Response.model_construct(id="r", usage=None),
                        type="response.completed",
                    ),
                )
            )
        observer.finish()
        observer.finish(RuntimeError("failure after the model"))
    assert [u.diagnostics.text_chars for u in state.llm_usages] == [5, 0, 11]
    assert [u.diagnostics.call_index for u in state.llm_usages] == [1, 2, 3]
    assert len({u.diagnostics.run_id for u in state.llm_usages}) == 1
    assert len({u.diagnostics.call_id for u in state.llm_usages}) == 3
    assert all(u.diagnostics.status == "succeeded" for u in state.llm_usages)
    assert len([r for r in caplog.records if r.msg == "llm.call.completed"]) == 3
    serialized = ChatResponseAssembler._serialize_llm_usages(state.llm_usages)
    assert serialized[0]["diagnostics"]["text_chars"] == 5
    assert ConversationTokenUsage.from_llm_usages(serialized).total_tokens == 21
    other = SdkModelCallObserver(runtime, logging.getLogger(__name__))
    call = other.start(phase="tool_loop", streamed=False)
    other.finish(RuntimeError("provider failure"))
    assert call.diagnostics.run_id != first.diagnostics.run_id
    assert call.diagnostics.call_index == 1
    assert state.llm_usages[-1].usage_reported is False
    assert ConversationTokenUsage.from_llm_usages(state.llm_usages).total_tokens == 21


def test_compaction_timeout_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = AgentRuntimeInput(
        llm_client=_PlainTextLlmClient(),
        request=ChatAskRequest(question="test"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )
    controller = _SdkModelInputController(
        runtime=runtime,
        model=_ObservedStreamModel("timeout"),
        model_settings=ModelSettings(timeout=0.04),
        turn_control=_SdkTurnControl(max_turns=100),
        run_config=RunConfig(tracing_disabled=True),
    )
    from agents import ModelTimeoutError

    with pytest.raises(ModelTimeoutError):
        asyncio.run(controller._summarize([{"role": "user", "content": "test"}]))
    usage = runtime.state.llm_usages[0]
    assert usage.phase == "context_compaction"
    assert usage.diagnostics.status == "timeout"
    assert usage.duration_ms >= 30
    assert usage.usage_reported is False


@pytest.mark.parametrize("endpoint", ["/chat/completions", "/responses"])
@pytest.mark.parametrize("top_p", [None, 0.0, 0.95, 1.0])
def test_native_sdk_sends_optional_top_p(endpoint: str, top_p: float | None) -> None:
    import httpx
    from openai import AsyncOpenAI

    from agents.models.interface import ModelTracing

    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if endpoint == "/chat/completions":
            return httpx.Response(200, json={
                "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        return httpx.Response(200, json={
            "id": "resp-test", "object": "response", "created_at": 0,
            "model": "test", "status": "completed", "output": [],
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        })

    async def run() -> None:
        connection = LlmConnectionConfig(
            base_url="https://llm.example/v1", model="test", endpoint_path=endpoint,
            top_p=top_p,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AsyncOpenAI(api_key="test-key", base_url=connection.base_url, http_client=http)
            model = AgentsSdkTurnRuntime._sdk_model(connection=connection, openai_client=client)
            await model.get_response(
                system_instructions="Test", input="Hello",
                model_settings=AgentsSdkTurnRuntime._model_settings(connection),
                tools=[], output_schema=None, handoffs=[], tracing=ModelTracing.DISABLED,
            )

    asyncio.run(run())
    assert len(captured) == 1
    if top_p is None:
        assert "top_p" not in captured[0]
    else:
        assert captured[0]["top_p"] == top_p
