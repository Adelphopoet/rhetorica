import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rhetorica_backend.contracts.chat import ChatAskRequest, ChunkEvent, ThinkingEvent
from rhetorica_backend.contracts.coding import (
    CodingChatPlan,
    CodingPlanDraft,
    CodingResourceSelection,
    CreateCodingRunRequest,
)
from rhetorica_backend.domain.coding.chat_planning import CodingChatPlanningService
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.service import CodingRunService


def resources():
    return CodingResourceSelection(
        root_agent_id="root",
        developer_agent_id="dev",
        qa_agent_id="qa",
        gitlab_connector_id="gitlab",
        project_id="group/project",
        target_branch="main",
        environment_profile_id="env",
    )


def draft():
    return CodingPlanDraft(
        task="Add net revenue",
        steps=["Inspect model", "Implement and test"],
        developer_brief="Change only the selected model",
        qa_brief="Independently rerun tests",
        acceptance_criteria=["100 - 15 = 85"],
    )


class Plans:
    state = None

    def __init__(self):
        self.state = {}

    @contextmanager
    def locked(self, **_):
        yield self.state

    def begin(self, *, generation_id, **_):
        self.state["coding_chat"] = {"generation_id": generation_id, "plan": None}

    def save(self, *, plan, **_):
        if plan.id != self.state["coding_chat"]["generation_id"]:
            raise CodingRunError("coding_plan_superseded")
        self.state["coding_chat"]["plan"] = plan.model_dump(mode="json")

    def attach_run(self, *, plan_id, run_id, **_):
        plan = self.state.get("coding_chat", {}).get("plan")
        if plan and plan["id"] == plan_id:
            plan["run_id"] = run_id

    def current(self, **_):
        value = self.state.get("coding_chat", {}).get("plan")
        return CodingChatPlan.model_validate(value) if value else None


def setup(answer=None):
    service = Mock(enabled=True)
    service.chats.get_conversation.return_value = SimpleNamespace(
        workspace_id="ws", agent_id="root"
    )
    messages = []

    def save(_cid, role, content, **_):
        message = SimpleNamespace(id=str(len(messages)), role=role, content=content)
        messages.append(message)
        return message

    service.chats.save_message.side_effect = save
    service.chats.get_messages.side_effect = lambda _cid, limit=40: messages[-limit:]
    service.create.return_value = SimpleNamespace(run=SimpleNamespace(id="run"))
    planner = SimpleNamespace(
        discuss=AsyncMock(return_value=answer or (draft(), "Plan with developer and QA"))
    )
    planning = CodingChatPlanningService(service=service, plans=Plans(), planner=planner)
    user = SimpleNamespace(id="owner", workspace_id="ws")
    request = ChatAskRequest(
        question="Add net_revenue",
        conversation_id="chat",
        agent_id="root",
        coding_mode=True,
        coding_resources=resources(),
    )
    return planning, user, request


def groom(planning, user, request):
    async def collect():
        return [e async for e in planning.stream(request=request, user=user)]

    return asyncio.run(collect())


def test_grooming_saves_regular_messages_and_plan_without_starting_specialists():
    planning, user, request = setup()
    events = groom(planning, user, request)
    assert events[-1].answer == "Plan with developer and QA"
    assert planning.current("chat", user).draft.qa_brief == "Independently rerun tests"
    planning.service.create.assert_not_called()
    assert planning.service.chats.save_message.call_count == 2


def test_questions_do_not_create_a_confirmation_or_a_run():
    planning, user, request = setup((None, "Which refunds should we subtract?"))
    groom(planning, user, request)
    assert planning.current("chat", user) is None
    planning.service.create.assert_not_called()


def test_live_log_arrives_before_model_finishes_and_cancel_releases_discussion():
    planning, user, request = setup()
    finalized = []

    async def discuss(**kwargs):
        kwargs["on_event"](ThinkingEvent(content="Reading repository guidance"))
        try:
            await asyncio.Event().wait()
        finally:
            finalized.append(True)

    planning.planner.discuss.side_effect = discuss

    async def collect():
        stream = planning.stream(request=request, user=user)
        first = await anext(stream)
        assert first.content == "Reading repository guidance"
        assert planning.plans.current() is None
        await stream.aclose()

    asyncio.run(collect())
    assert finalized == [True]
    planning.service.create.assert_not_called()
    assert planning.service.chats.save_message.call_count == 1


def test_streamed_clarification_is_saved_once_without_duplicate_answer_chunks():
    planning, user, request = setup()

    async def discuss(**kwargs):
        kwargs["on_event"](ChunkEvent(content="Какой "))
        kwargs["on_event"](ChunkEvent(content="период?"))
        return None, "Какой период?"

    planning.planner.discuss.side_effect = discuss
    events = groom(planning, user, request)
    assert [event.type.value for event in events] == ["chunk", "chunk", "done"]
    assert events[-1].answer == "Какой период?"
    saved = planning.service.chats.save_message.call_args
    assert saved.args[1:] == ("assistant", "Какой период?")
    assert planning.current("chat", user) is None
    planning.service.create.assert_not_called()


def test_confirm_uses_only_persisted_plan_and_stable_admission_key():
    planning, user, request = setup()
    groom(planning, user, request)
    plan = planning.current("chat", user)
    for _ in range(2):
        assert planning.confirm(conversation_id="chat", plan_id=plan.id, user=user).run.id == "run"
    calls = planning.service.create.call_args_list
    assert calls[0].kwargs["idempotency_key"] == calls[1].kwargs["idempotency_key"]
    assert calls[0].kwargs["confirmed_plan_id"] == plan.id
    assert planning.current("chat", user).run_id == "run"
    assert "Independent QA" in calls[0].args[0].task


def test_new_message_invalidates_confirmation_and_new_plan_supersedes_old():
    planning, user, request = setup()
    groom(planning, user, request)
    old = planning.current("chat", user)
    planning.service.chats.save_message("chat", "user", "Change acceptance")
    assert planning.current("chat", user) is None
    with pytest.raises(CodingRunError, match="coding_plan_superseded"):
        planning.confirm(conversation_id="chat", plan_id=old.id, user=user)
    groom(planning, user, request)
    with pytest.raises(CodingRunError, match="coding_plan_superseded"):
        planning.confirm(conversation_id="chat", plan_id=old.id, user=user)
    planning.service.create.assert_not_called()


def test_revocation_blocks_next_confirmation_before_admission():
    planning, user, request = setup()
    groom(planning, user, request)
    plan = planning.current("chat", user)
    planning.service.require_actor.side_effect = CodingRunError(
        "workspace_access_denied", status_code=403
    )
    with pytest.raises(CodingRunError):
        planning.confirm(conversation_id="chat", plan_id=plan.id, user=user)
    planning.service.create.assert_not_called()


def test_direct_web_admission_requires_confirmation_before_resource_or_sandbox_access():
    service = object.__new__(CodingRunService)
    service.resources = Mock()
    service.resources.require_actor.return_value = Mock()
    with pytest.raises(CodingRunError, match="coding_confirmation_required"):
        service.create(
            CreateCodingRunRequest(task="code", **resources().model_dump()),
            Mock(),
            auth_kind="session",
            idempotency_key="key",
        )
    service.resources.validate_resources.assert_not_called()


def test_pinned_coordinator_cannot_be_replaced_by_client_resource_fields():
    planning, user, request = setup()
    selected = planning.selection(
        request.model_copy(
            update={
                "coding_resources": resources().model_copy(update={"root_agent_id": "attacker"})
            }
        ),
        user,
    )
    assert selected.root_agent_id == "root"
    with pytest.raises(CodingRunError, match="coding_root_conversation_mismatch"):
        planning.selection(request.model_copy(update={"agent_id": "attacker"}), user)
