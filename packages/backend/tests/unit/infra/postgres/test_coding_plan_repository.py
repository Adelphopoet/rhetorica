import pytest
from rhetorica_backend.contracts.coding import (
    CodingChatPlan,
    CodingPlanDraft,
    CodingResourceSelection,
)
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.coding_plan_repository import CodingPlanRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository


def test_plan_is_owner_scoped_and_superseded_generation_cannot_overwrite_it(tmp_path):
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'plan.db'}")
    db.create_schema()
    users = UserAccountRepository(db)
    owner = users.ensure_bootstrap_user("default")
    other = users.create_local_user(
        workspace_id="default", login="other", password_hash="test-only", role="analyst"
    )
    chats = ChatRepository(db)
    conversation = chats.create_conversation("default", owner_user_id=owner.id)
    chats.update_conversation_state(
        conversation.id, {"analytics": "preserve"}, owner_user_id=owner.id
    )
    plans = CodingPlanRepository(db)
    plans.begin(conversation_id=conversation.id, user=owner, generation_id="first")
    plan = CodingChatPlan(
        id="first",
        conversation_id=conversation.id,
        resources=CodingResourceSelection(
            root_agent_id="root",
            developer_agent_id="dev",
            qa_agent_id="qa",
            gitlab_connector_id="gitlab",
            project_id="project",
            target_branch="main",
            environment_profile_id="env",
        ),
        draft=CodingPlanDraft(
            task="code",
            steps=["Implement"],
            developer_brief="Develop",
            qa_brief="Check",
            acceptance_criteria=["Tests pass"],
        ),
    )
    plans.save(plan=plan, user=owner)
    assert plans.current(conversation_id=conversation.id, user=owner) == plan
    with pytest.raises(CodingRunError, match="coding_conversation_required"):
        plans.current(conversation_id=conversation.id, user=other)
    assert chats.get_conversation_state(conversation.id)["analytics"] == "preserve"
    plans.begin(conversation_id=conversation.id, user=owner, generation_id="second")
    with pytest.raises(CodingRunError, match="coding_plan_superseded"):
        plans.save(plan=plan, user=owner)
    assert plans.current(conversation_id=conversation.id, user=owner) is None


def test_analytics_turn_state_update_preserves_coding_plan_state(tmp_path):
    db = DatabaseRuntime(f"sqlite+pysqlite:///{tmp_path / 'plan.db'}")
    db.create_schema()
    owner = UserAccountRepository(db).ensure_bootstrap_user("default")
    chats = ChatRepository(db)
    conversation = chats.create_conversation("default", owner_user_id=owner.id)
    plans = CodingPlanRepository(db)
    plans.begin(conversation_id=conversation.id, user=owner, generation_id="first")
    chats.update_conversation_state(
        conversation.id, {"state_revision": 3}, owner_user_id=owner.id
    )
    state = chats.get_conversation_state(conversation.id, owner_user_id=owner.id)
    assert state["state_revision"] == 3
    assert state["coding_chat"]["generation_id"] == "first"
