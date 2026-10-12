"""Conversation grooming and explicit owner confirmation of a persisted plan."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from rhetorica_backend.contracts.chat import ChunkEvent, DoneEvent, StreamEvent
from rhetorica_backend.contracts.coding import (
    CodingChatPlan,
    CodingResourceSelection,
    CreateCodingRunRequest,
)
from rhetorica_backend.domain.chat.journal import ChatJournal
from rhetorica_backend.domain.coding.errors import CodingRunError


class CodingChatPlanningService:
    def __init__(self, *, service, plans, planner):
        self.service, self.plans, self.planner = service, plans, planner

    def conversation(self, conversation_id, user):
        conversation = self.service.chats.get_conversation(
            conversation_id or "", owner_user_id=user.id
        )
        if conversation is None or conversation.workspace_id != user.workspace_id:
            raise CodingRunError("coding_conversation_required", status_code=404)
        return conversation

    def selection(self, request, user):
        self.service.require_actor(user, auth_kind="session", write=True)
        if not self.service.enabled:
            raise CodingRunError("coding_disabled", status_code=503)
        conversation = self.conversation(request.conversation_id, user)
        if not request.agent_id or conversation.agent_id != request.agent_id:
            raise CodingRunError("coding_root_conversation_mismatch", status_code=409)
        selected = request.coding_resources
        if selected is None:
            defaults = self.service.capabilities(user, root_agent_id=request.agent_id)[
                "launch_defaults"
            ]
            if defaults is None:
                raise CodingRunError("coding_resources_required")
            selected = CodingResourceSelection.model_validate(defaults)
        selected = selected.model_copy(update={"root_agent_id": conversation.agent_id})
        self.service.validate_resources(
            CreateCodingRunRequest(task="Coding planning", **selected.model_dump()), user
        )
        return selected

    async def stream(self, *, request, user):
        resources = self.selection(request, user)
        if request.attachment_ids:
            raise CodingRunError("coding_planning_attachments_not_supported")
        generation_id = str(uuid4())
        await asyncio.to_thread(
            self.plans.begin,
            conversation_id=request.conversation_id,
            user=user,
            generation_id=generation_id,
        )
        await asyncio.to_thread(
            self.service.chats.save_message,
            request.conversation_id,
            "user",
            request.question,
            owner_user_id=user.id,
            agent_id=request.agent_id,
        )
        messages = self.service.chats.get_messages(request.conversation_id, limit=40)
        profile = self.service.agent(user, request.agent_id)
        events: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

        async def discuss():
            try:
                return await self.planner.discuss(
                    user=user,
                    profile=profile,
                    resources=resources,
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                    on_event=events.put_nowait,
                    conversation_id=request.conversation_id,
                )
            finally:
                events.put_nowait(None)

        discussion = asyncio.create_task(discuss())
        answer_streamed = False
        journal = ChatJournal()
        try:
            while (event := await events.get()) is not None:
                journal.record(event)
                answer_streamed = answer_streamed or isinstance(event, ChunkEvent)
                yield event
            draft, answer = await discussion
        finally:
            if not discussion.done():
                discussion.cancel()
                with suppress(asyncio.CancelledError):
                    await discussion
        message = await asyncio.to_thread(
            self.service.chats.save_message,
            request.conversation_id,
            "assistant",
            answer,
            journal_events=journal.events,
            owner_user_id=user.id,
            agent_id=request.agent_id,
        )
        if draft:
            await asyncio.to_thread(
                self.plans.save,
                user=user,
                plan=CodingChatPlan(
                    id=generation_id,
                    conversation_id=request.conversation_id,
                    resources=resources,
                    draft=draft,
                    assistant_message_id=message.id,
                ),
            )
        if not answer_streamed:
            yield ChunkEvent(content=answer)
        yield DoneEvent(
            answer=answer, conversation_id=request.conversation_id, assistant_message_id=message.id
        )

    def current(self, conversation_id, user):
        self.service.require_actor(user, auth_kind="session")
        self.conversation(conversation_id, user)
        plan = self.plans.current(conversation_id=conversation_id, user=user)
        messages = self.service.chats.get_messages(conversation_id, limit=1)
        return (
            plan
            if plan and (plan.run_id or (messages and messages[-1].id == plan.assistant_message_id))
            else None
        )

    def confirm(self, *, conversation_id, plan_id, user):
        self.service.require_actor(user, auth_kind="session", write=True)
        self.conversation(conversation_id, user)
        plan = self.plans.current(conversation_id=conversation_id, user=user)
        if plan is None or plan.id != plan_id:
            raise CodingRunError("coding_plan_superseded", status_code=409)
        messages = self.service.chats.get_messages(conversation_id, limit=1)
        if not plan.run_id and (not messages or messages[-1].id != plan.assistant_message_id):
            raise CodingRunError("coding_plan_superseded", status_code=409)
        # Admission talks to GitLab and artifact storage, so no conversation lock is held here.
        # The admission key is derived from the plan id: concurrent confirmations share one run.
        result = self.service.create(
            CreateCodingRunRequest(
                **plan.resources.model_dump(),
                conversation_id=conversation_id,
                task=plan.draft.instruction(),
            ),
            user,
            auth_kind="session",
            idempotency_key="confirmed-plan:" + plan.id,
            confirmed_plan_id=plan.id,
            plan_draft=plan.draft,
        )
        self.plans.attach_run(
            conversation_id=conversation_id, user=user, plan_id=plan.id, run_id=result.run.id
        )
        return result
