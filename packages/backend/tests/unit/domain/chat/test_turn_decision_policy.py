from __future__ import annotations

import logging

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.models import FinalAnswerDecision
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.turn_decision_policy import (
    TurnDecisionPolicy,
)


def _policy() -> TurnDecisionPolicy:
    return TurnDecisionPolicy(
        logger=logging.getLogger(__name__),
        ensure_dynamic_tools=lambda **kwargs: kwargs["tools"],
        assistant_message_for_history=lambda message: message,
        synthetic_assistant_tool_message=lambda **kwargs: kwargs,
        protocol_repair_message=lambda _tool_names: {"role": "system", "content": "repair"},
        forced_tool_repair_message=lambda: {"role": "system", "content": "forced"},
        extract_message_content=lambda message: str(message.get("content") or ""),
        extract_tool_name=lambda _tool_call: None,
        extract_tool_arguments=lambda _tool_call: {},
        emit_diagnostic_event=lambda *_args, **_kwargs: None,
        decorate_final_answer=lambda **kwargs: kwargs["answer"],
    )


def test_turn_decision_policy_defers_structured_json_to_completion_policy() -> None:
    policy = _policy()
    state = ToolOrchestrationState()

    decision = policy.resolve_non_tool_response(
        llm_client=object(),
        messages=[],
        message={"content": '{"answer_markdown":"Привет!","sql":null,"target":null}'},
        raw_content='{"answer_markdown":"Привет!","sql":null,"target":null}',
        current_tools=[],
        finalizable_content='{"answer_markdown":"Привет!","sql":null,"target":null}',
        protocol_repair_enabled=False,
        request=ChatAskRequest(question="привет", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert decision is None


def test_turn_decision_policy_still_direct_finalizes_plain_text_without_tools() -> None:
    policy = _policy()
    state = ToolOrchestrationState()

    decision = policy.resolve_non_tool_response(
        llm_client=object(),
        messages=[],
        message={"content": "Привет!"},
        raw_content="Привет!",
        current_tools=[],
        finalizable_content="Привет!",
        protocol_repair_enabled=False,
        request=ChatAskRequest(question="привет", workspace_id="default"),
        db_connectors=[],
        state=state,
    )

    assert isinstance(decision, FinalAnswerDecision)
    assert decision.raw_final_text == "Привет!"
    assert decision.finalization_mode == "direct"
