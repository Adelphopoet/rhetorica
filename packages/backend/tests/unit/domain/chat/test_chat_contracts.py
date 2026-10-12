from __future__ import annotations

from rhetorica_backend.contracts.chat import MessageItem


def test_message_item_accepts_agent_runtime_trace() -> None:
    message = MessageItem.model_validate(
        {
            "id": "msg-1",
            "conversation_id": "conv-1",
            "role": "assistant",
            "content": "Привет.",
            "agent_id": "2b974e68-4f4f-4673-85b8-6038018e91b6",
            "created_at": "2026-05-22T11:30:00Z",
            "capability_trace": {
                "available_tools": [],
                "used_tools": [],
                "used_providers": [],
                "runtime_context": {
                    "agent": {
                        "id": "2b974e68-4f4f-4673-85b8-6038018e91b6",
                        "has_prompt": True,
                        "llm_connector_name": "example-llm",
                    }
                },
            },
        }
    )

    assert message.capability_trace.runtime_context is not None
    assert message.capability_trace.runtime_context.agent is not None
    assert (
        message.capability_trace.runtime_context.agent.id
        == "2b974e68-4f4f-4673-85b8-6038018e91b6"
    )
