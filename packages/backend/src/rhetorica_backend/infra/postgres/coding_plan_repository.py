"""Atomic conversation-owned plan state, isolated from analytics checkpoints."""

from collections.abc import Iterator
from contextlib import contextmanager

from rhetorica_backend.contracts.coding import CodingChatPlan
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.chat_models import ChatConversationOrm
from sqlalchemy import select


class CodingPlanRepository:
    def __init__(self, db):
        self._db = db

    @contextmanager
    def locked(self, *, conversation_id: str, user) -> Iterator[dict]:
        with self._db.session() as session:
            row = session.execute(
                select(ChatConversationOrm)
                .where(
                    ChatConversationOrm.id == conversation_id,
                    ChatConversationOrm.workspace_id == user.workspace_id,
                    ChatConversationOrm.owner_user_id == user.id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
                # Serialize plan writers while permitting admission's FK KEY SHARE
                # in its own transaction; FOR UPDATE would deadlock that insert.
                .with_for_update(key_share=True)
            ).scalar_one_or_none()
            if row is None:
                raise CodingRunError("coding_conversation_required", status_code=404)
            state = dict(row.conversation_state or {})
            yield state
            row.conversation_state = state
            session.commit()

    def begin(self, *, conversation_id, user, generation_id):
        with self.locked(conversation_id=conversation_id, user=user) as state:
            state["coding_chat"] = {"generation_id": generation_id, "plan": None}

    def save(self, *, plan: CodingChatPlan, user):
        with self.locked(conversation_id=plan.conversation_id, user=user) as state:
            current = state.get("coding_chat", {})
            if current.get("generation_id") != plan.id:
                raise CodingRunError("coding_plan_superseded", status_code=409)
            state["coding_chat"] = {**current, "plan": plan.model_dump(mode="json")}

    def attach_run(self, *, conversation_id, user, plan_id, run_id):
        """Link an admitted run unless a newer plan generation has replaced the confirmed one."""
        with self.locked(conversation_id=conversation_id, user=user) as state:
            current = state.get("coding_chat", {})
            plan = current.get("plan")
            if plan and plan.get("id") == plan_id:
                state["coding_chat"] = {**current, "plan": {**plan, "run_id": run_id}}

    def current(self, *, conversation_id, user):
        # Read-only: polling must not take a row lock or write the conversation.
        with self._db.session() as session:
            row = session.execute(
                select(ChatConversationOrm.conversation_state).where(
                    ChatConversationOrm.id == conversation_id,
                    ChatConversationOrm.workspace_id == user.workspace_id,
                    ChatConversationOrm.owner_user_id == user.id,
                    ChatConversationOrm.deleted_at.is_(None),
                )
            ).first()
        if row is None:
            raise CodingRunError("coding_conversation_required", status_code=404)
        value = (row[0] or {}).get("coding_chat", {}).get("plan")
        return CodingChatPlan.model_validate(value) if value else None
