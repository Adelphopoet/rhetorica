from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from rhetorica_backend.infra.postgres.agent_session import (
    AgentSessionConversationNotFoundError,
    PostgresAgentSessionStore,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import event


def _session_store(tmp_path: Path):
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/agent_session.db")

    @event.listens_for(runtime.engine, "connect")
    def enable_foreign_keys(
        connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    runtime.create_schema()
    repository = ChatRepository(runtime)
    conversation = repository.create_conversation(workspace_id="ws-1", title="Session")
    return (
        runtime,
        repository,
        conversation,
        PostgresAgentSessionStore(
            runtime,
            conversation.owner_user_id,
        ),
    )


@pytest.mark.parametrize("as_tool", [False, True])
def test_stateless_responses_replays_encrypted_reasoning_through_tools_and_session(
    tmp_path, as_tool
):
    import json

    import httpx
    from openai import AsyncOpenAI
    from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
    from rhetorica_backend.domain.chat.llm.sdk_models import build_model_settings, build_sdk_model

    from agents import Agent, RunConfig, Runner, function_tool
    from agents.tool_context import ToolContext

    runtime, _, conversation, store = _session_store(tmp_path)
    captured = []
    reasoning = {
        "id": "rs_test",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "opaque-provider-reasoning",
    }

    def response(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body["store"] is False
        assert "previous_response_id" not in body
        assert "conversation" not in body
        if len(captured) == 1:
            # Current OpenAI stateless Responses returns encrypted content by default.
            output = [
                reasoning,
                {
                    "id": "fc_test",
                    "type": "function_call",
                    "call_id": "call_test",
                    "name": "lookup",
                    "arguments": "{}",
                    "status": "completed",
                },
            ]
        else:
            replayed = [item for item in body["input"] if item.get("type") == "reasoning"]
            assert len(replayed) == 1
            assert replayed[0]["encrypted_content"] == reasoning["encrypted_content"]
            assert replayed[0]["id"] == reasoning["id"]
            assert any(
                item.get("type") == "function_call_output" and item["call_id"] == "call_test"
                for item in body["input"]
            )
            output = [
                {
                    "id": f"msg_{len(captured)}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(captured)}",
                "object": "response",
                "created_at": 0,
                "model": "test",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
            },
        )

    @function_tool
    def lookup() -> str:
        return "one"

    async def run():
        connection = LlmConnectionConfig(
            base_url="https://llm.example/v1",
            endpoint_path="/responses",
            model="test",
            extra_settings={"store": False},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            client = AsyncOpenAI(api_key="test", base_url=connection.base_url, http_client=http)
            agent = Agent(
                name="Specialist",
                model=build_sdk_model(connection=connection, openai_client=client),
                model_settings=build_model_settings(connection),
                tools=[lookup],
            )
            session = store.for_conversation(conversation.id)
            for operation, question in [("first", "Look up one"), ("second", "Continue")]:
                store.begin_operation(session.session_id, operation)
                config = RunConfig(tracing_disabled=True)
                if as_tool:
                    tool = agent.as_tool(
                        tool_name="specialist",
                        tool_description="Specialist",
                        session=session,
                        run_config=config,
                        failure_error_function=None,
                    )
                    arguments = json.dumps({"input": question})
                    result = await tool.on_invoke_tool(
                        ToolContext(
                            context=None,
                            tool_name=tool.name,
                            tool_call_id=operation,
                            tool_arguments=arguments,
                        ),
                        arguments,
                    )
                    assert result == "Done"
                else:
                    result = await Runner.run(agent, question, session=session, run_config=config)
                    assert result.final_output == "Done"
                # Reload through the application's durable Session implementation.
                session = PostgresAgentSessionStore(
                    runtime, conversation.owner_user_id
                ).for_conversation(conversation.id)
            return await session.get_items()

    items = asyncio.run(run())
    assert len(captured) == 3
    assert captured[2]["input"][-1] == {"role": "user", "content": "Continue"}
    assert (
        next(item for item in items if item.get("type") == "reasoning")["encrypted_content"]
        == reasoning["encrypted_content"]
    )


def test_agent_session_bootstraps_legacy_history_once(tmp_path: Path) -> None:
    _, repository, conversation, store = _session_store(tmp_path)
    repository.save_message(
        conversation_id=conversation.id,
        role="user",
        content="legacy question",
        owner_user_id=conversation.owner_user_id,
    )
    repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="legacy answer",
        owner_user_id=conversation.owner_user_id,
    )

    agent_session = store.for_conversation(conversation.id)
    assert asyncio.run(agent_session.get_items()) == [
        {"role": "user", "content": "legacy question"},
        {"role": "assistant", "content": "legacy answer"},
    ]

    repository.save_message(
        conversation_id=conversation.id,
        role="assistant",
        content="must not be bootstrapped twice",
        owner_user_id=conversation.owner_user_id,
    )
    assert asyncio.run(store.for_conversation(conversation.id).get_items())[-1] == {
        "role": "assistant",
        "content": "legacy answer",
    }


def test_agent_session_supports_sdk_history_operations_and_atomic_replace(
    tmp_path: Path,
) -> None:
    _, _, conversation, store = _session_store(tmp_path)
    agent_session = store.for_conversation(conversation.id)

    asyncio.run(
        agent_session.add_items([
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ])
    )
    assert asyncio.run(agent_session.get_items(limit=1)) == [
        {"role": "assistant", "content": "two"}
    ]
    assert asyncio.run(agent_session.pop_item()) == {
        "role": "assistant",
        "content": "two",
    }

    checkpoint = {"role": "user", "content": "Rhetorica context checkpoint v1"}
    asyncio.run(agent_session.replace_items([checkpoint]))
    assert asyncio.run(agent_session.get_items()) == [checkpoint]

    asyncio.run(agent_session.clear_session())
    assert asyncio.run(agent_session.get_items()) == []
    assert asyncio.run(store.for_conversation(conversation.id).get_items()) == []


def test_agent_session_enforces_conversation_owner(tmp_path: Path) -> None:
    runtime, _, conversation, _ = _session_store(tmp_path)

    with pytest.raises(AgentSessionConversationNotFoundError):
        PostgresAgentSessionStore(runtime, "different-owner").for_conversation(conversation.id)


def test_context_projection_is_separate_and_replaced_atomically(tmp_path: Path) -> None:
    _, repository, conversation, store = _session_store(tmp_path)
    session = store.for_conversation(conversation.id)
    metadata = {
        "version": 2,
        "tool_mode": "dispatch",
        "events": [
            {"offset": 1, "anchor": "hash", "message": {"role": "user", "content": "service"}}
        ],
    }
    asyncio.run(session.set_context_metadata(metadata))
    assert asyncio.run(session.get_items()) == []
    restored = store.for_conversation(conversation.id)
    assert asyncio.run(restored.get_context_metadata()) == metadata
    asyncio.run(
        restored.replace_items(
            [{"role": "user", "content": "checkpoint"}],
            context_metadata={"tool_mode": "dispatch", "events": []},
        )
    )
    assert asyncio.run(restored.get_context_metadata()) == {"tool_mode": "dispatch", "events": []}
    asyncio.run(restored.clear_session())
    assert asyncio.run(restored.get_context_metadata()) == {}


def test_operation_replay_replaces_only_its_suffix_and_next_operation_keeps_history(tmp_path):
    _, _, conversation, store = _session_store(tmp_path)
    session = store.for_conversation(conversation.id)
    asyncio.run(session.add_items([{"role": "user", "content": "previous operation"}]))
    store.begin_operation(session.session_id, "develop:0")
    asyncio.run(session.add_items([{"role": "assistant", "content": "interrupted"}]))
    store.begin_operation(session.session_id, "develop:0")
    assert asyncio.run(session.get_items()) == [{"role": "user", "content": "previous operation"}]
    asyncio.run(session.add_items([{"role": "assistant", "content": "completed"}]))
    store.begin_operation(session.session_id, "develop:1")
    assert len(asyncio.run(session.get_items())) == 2
    asyncio.run(session.add_items([{"role": "user", "content": "fix QA finding"}]))
    store.begin_operation(session.session_id, "develop:1")
    assert [item["content"] for item in asyncio.run(session.get_items())] == [
        "previous operation",
        "completed",
    ]


def test_chat_session_rejects_wrong_workspace_on_initialize_and_read(tmp_path):
    runtime, _, conversation, store = _session_store(tmp_path)
    store.for_conversation(conversation.id)
    foreign = PostgresAgentSessionStore(runtime, conversation.owner_user_id, workspace_id="foreign")
    with pytest.raises(AgentSessionConversationNotFoundError):
        foreign.for_conversation(conversation.id)
    with pytest.raises(AgentSessionConversationNotFoundError):
        foreign.get_items(conversation.id)


@pytest.mark.parametrize("as_tool", [False, True])
@pytest.mark.parametrize("provider_store", [None, False, True])
def test_native_sdk_runner_persists_and_continues_shared_session(tmp_path, as_tool, provider_store):
    import json

    from openai.types.responses import ResponseOutputMessage, ResponseOutputText
    from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
    from rhetorica_backend.domain.chat.llm.sdk_models import build_model_settings

    from agents import Agent, RunConfig, Runner
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    runtime, repository, conversation, store = _session_store(tmp_path)
    inputs = []

    class ScriptedModel(Model):
        async def get_response(self, *args, **kwargs):
            assert kwargs["model_settings"].store is provider_store
            inputs.append(kwargs["input"])
            return ModelResponse(
                output=[
                    ResponseOutputMessage(
                        id=f"message-{len(inputs)}",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(type="output_text", text="completed", annotations=[])
                        ],
                        type="message",
                    )
                ],
                usage=Usage(requests=1),
                response_id=None,
            )

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("not a streaming test")
            yield

    async def run():
        connection = LlmConnectionConfig(
            base_url="https://llm.example/v1",
            model="test",
            extra_settings={} if provider_store is None else {"store": provider_store},
        )
        agent = Agent(
            name="Configured specialist",
            model=ScriptedModel(),
            model_settings=build_model_settings(connection),
        )
        session = store.for_conversation(conversation.id)
        for operation, question in [("first", "create a model"), ("second", "fix QA finding")]:
            store.begin_operation(session.session_id, operation)
            config = RunConfig(tracing_disabled=True)
            if as_tool:
                tool = agent.as_tool(
                    tool_name="configured_profile",
                    tool_description="Configured specialist",
                    session=session,
                    run_config=config,
                    failure_error_function=None,
                )
                arguments = json.dumps({"input": question})
                result = await tool.on_invoke_tool(
                    ToolContext(
                        context=None,
                        tool_name=tool.name,
                        tool_call_id=operation,
                        tool_arguments=arguments,
                    ),
                    arguments,
                )
                assert result == "completed"
            else:
                await Runner.run(agent, question, session=session, run_config=config)
            for role, content in [("user", question), ("assistant", "completed")]:
                repository.save_message(
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                    owner_user_id=conversation.owner_user_id,
                )
        return await session.get_items()

    items = asyncio.run(run())
    assert len(items) == 4
    restored = PostgresAgentSessionStore(runtime, conversation.owner_user_id).for_conversation(
        conversation.id
    )
    assert asyncio.run(restored.get_items()) == items
    history = repository.get_messages(conversation_id=conversation.id)
    assert [item.content for item in history] == [
        "create a model",
        "completed",
        "fix QA finding",
        "completed",
    ]
    assert inputs[1][0] == {"role": "user", "content": "create a model"}
    assert inputs[1][-1] == {"role": "user", "content": "fix QA finding"}
