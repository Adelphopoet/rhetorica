from fastapi import HTTPException
from rhetorica_backend.contracts.agents import AgentProfileItem
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.agent_profile_resolver import (
    AgentProfileResolutionError,
    AgentProfileResolver,
)
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatConversationItem


def resolve_agent_for_chat(
    *,
    request: ChatAskRequest,
    agent_repository: AgentProfileRepository,
    current_user_id: str,
    existing_conversation: ChatConversationItem | None = None,
) -> tuple[ChatAskRequest, AgentProfileItem | None]:
    """Translate shared agent policy failures into the HTTP route contract."""
    try:
        return AgentProfileResolver(agent_repository).resolve(
            request=request,
            current_user_id=current_user_id,
            existing_conversation=existing_conversation,
        )
    except AgentProfileResolutionError as exc:
        status_code = 409 if exc.code == "conversation_agent_locked" else 404
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
