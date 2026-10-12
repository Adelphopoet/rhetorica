from functools import lru_cache

from celery import Celery  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from rhetorica_backend.contracts import (
    ConversationDetailItem,
    ConversationItem,
    ConversationTokenUsageItem,
    CreateConversationRequest,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    RepairTurnRequest,
    RepairTurnResponse,
)
from rhetorica_backend.contracts.chat import FeedbackReason
from rhetorica_backend.domain.chat.feedback import (
    FeedbackContext,
    FeedbackPipelineService,
    FeedbackPolicy,
    FeedbackRoutingAction,
    RepairTurnService,
)
from rhetorica_backend.domain.files.service import ArtifactService
from rhetorica_backend.domain.rag import RagPipelineTaskRequest
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.human_review_feedback_service import (
    HumanReviewFeedbackService,
)

from rhetorica_api.app.identity import get_db_runtime, resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/chat", tags=["chat"])
logger = get_logger(__name__)


def _token_usage_item(conversation) -> ConversationTokenUsageItem:
    usage = conversation.token_usage
    return ConversationTokenUsageItem(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_input_tokens=usage.cache_write_input_tokens,
        cache_usage_reported_calls=usage.cache_usage_reported_calls,
        total_tokens=usage.total_tokens,
    )


def _get_feedback_pipeline_service() -> FeedbackPipelineService:
    """Get the feedback pipeline service (created per-request for DI)."""
    policy = FeedbackPolicy(logger=logger)
    return FeedbackPipelineService(policy=policy, logger=logger)


def _runtime_context_from_message(msg) -> dict:
    trace = msg.capability_trace if isinstance(msg.capability_trace, dict) else {}
    runtime_context = trace.get("runtime_context")
    return runtime_context if isinstance(runtime_context, dict) else {}


@lru_cache(maxsize=1)
def _task_queue() -> Celery:
    return Celery("rhetorica-api", broker=settings.celery_broker_url)


def _dispatch_rag_feedback_pipeline(task_request: RagPipelineTaskRequest) -> None:
    task_payload = task_request.model_dump(mode="json")
    task_logger = logger.bind(**task_payload)
    try:
        task_logger.info("chat.rag_feedback.enqueue.started")
        _task_queue().send_task(
            "rag_insights.process_feedback",
            kwargs={"payload": task_payload},
        )
        task_logger.info("chat.rag_feedback.enqueue.completed")
    except Exception:
        task_logger.exception("chat.rag_feedback.enqueue.failed")


@router.post("/conversation", response_model=ConversationItem)
def create_conversation(payload: CreateConversationRequest) -> ConversationItem:
    """Create a new chat conversation."""
    current_user = resolve_current_user(payload.workspace_id)
    db_runtime = get_db_runtime()
    if payload.agent_id is not None:
        agent = AgentProfileRepository(db=db_runtime).get(
            agent_id=payload.agent_id,
            workspace_id=payload.workspace_id,
            current_user_id=current_user.id,
            active_only=True,
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="agent_not_found")
    chat_repo = ChatRepository(db=db_runtime)
    conversation = chat_repo.create_conversation(
        workspace_id=payload.workspace_id,
        title=payload.title,
        owner_user_id=current_user.id,
        agent_id=payload.agent_id,
    )
    return ConversationItem(
        id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        agent_id=conversation.agent_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        message_count=0,
        token_usage=_token_usage_item(conversation),
    )


@router.get("/conversation/{conversation_id}", response_model=ConversationDetailItem)
def get_conversation(conversation_id: str) -> ConversationDetailItem:
    """Get a conversation with its messages."""
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    current_user = resolve_current_user(summary.workspace_id)
    conversation = chat_repo.get_conversation_with_messages(
        conversation_id=conversation_id,
        message_limit=50,
        owner_user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetailItem(
        id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        agent_id=conversation.agent_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        token_usage=_token_usage_item(conversation),
        messages=[
            {
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "role": msg.role,
                "content": msg.content,
                "agent_id": msg.agent_id,
                "created_at": msg.created_at.isoformat(),
                "generated_sql": msg.generated_sql,
                "execution_id": msg.execution_id,
                "context_tables": msg.context_tables,
                "metadata_sources": msg.metadata_sources,
                "claims": msg.claims,
                "evidence": msg.evidence,
                "claim_evidence_links": msg.claim_evidence_links,
                "uncertainty_flags": msg.uncertainty_flags,
                "capability_trace": msg.capability_trace,
                "optimization_trace": msg.optimization_trace,
                "selected_connector": msg.selected_connector,
                "analysis_document": msg.analysis_document,
                "visualization": msg.visualization_spec,
                "warnings": msg.warnings,
                "feedback": msg.feedback,
                "feedback_updated_at": msg.feedback_updated_at.isoformat()
                if msg.feedback_updated_at
                else None,
                "feedback_reason": msg.feedback_reason,
                "feedback_comment": msg.feedback_comment,
                "feedback_what_went_wrong": msg.feedback_what_went_wrong,
                "feedback_expected_answer": msg.feedback_expected_answer,
                "files": [ArtifactService.to_ref(file).model_dump() for file in msg.files],
            }
            for msg in conversation.messages
        ],
    )


@router.get("/conversations", response_model=list[ConversationItem])
def list_conversations(workspace_id: str = "default", limit: int = 50) -> list[ConversationItem]:
    """List conversations for a workspace."""
    current_user = resolve_current_user(workspace_id)
    chat_repo = ChatRepository(db=get_db_runtime())
    conversations = chat_repo.list_conversations(
        workspace_id=workspace_id,
        limit=limit,
        owner_user_id=current_user.id,
    )
    return [
        ConversationItem(
            id=conv.id,
            workspace_id=conv.workspace_id,
            title=conv.title,
            agent_id=conv.agent_id,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=getattr(conv, "message_count", len(conv.messages)),
            token_usage=_token_usage_item(conv),
        )
        for conv in conversations
    ]


@router.delete("/conversation/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    """Hide a conversation while retaining its persisted data."""
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    current_user = resolve_current_user(summary.workspace_id)
    deleted = chat_repo.delete_conversation(
        conversation_id=conversation_id,
        owner_user_id=current_user.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/message/{message_id}/feedback", response_model=MessageFeedbackResponse)
def set_message_feedback(
    message_id: str, payload: MessageFeedbackRequest
) -> MessageFeedbackResponse:
    """Set analyst feedback for assistant message.

    Supports extended feedback with reason and comment for negative feedback types.
    """
    current_user = resolve_current_user(payload.workspace_id)
    chat_repo = ChatRepository(db=get_db_runtime())
    conversation = chat_repo.get_conversation(
        payload.conversation_id,
        owner_user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.workspace_id != payload.workspace_id:
        raise HTTPException(status_code=403, detail="Conversation does not belong to workspace")

    message = chat_repo.get_message(
        payload.conversation_id,
        message_id,
        owner_user_id=current_user.id,
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.role != "assistant":
        raise HTTPException(
            status_code=400, detail="Feedback is allowed only for assistant messages"
        )

    # Build feedback context for policy routing
    feedback_context = FeedbackContext(
        message_id=message_id,
        conversation_id=payload.conversation_id,
        workspace_id=payload.workspace_id,
        feedback=payload.feedback,
        reason=payload.reason,
        comment=payload.comment,
        context_tables=message.context_tables or [],
        selected_connector=message.selected_connector,
    )

    # Process feedback through typed policy
    pipeline_service = _get_feedback_pipeline_service()
    routing_decision = pipeline_service.process_feedback(feedback_context)

    logger.info(
        "feedback.route.processed",
        extra={
            "message_id": message_id,
            "feedback": payload.feedback.value,
            "reason": payload.reason.value if payload.reason else None,
            "routing_decision": routing_decision.reason,
        },
    )

    # The message and its external projection must either commit together or
    # neither must be visible to the caller. Celery is submitted afterwards.
    saved = HumanReviewFeedbackService(db=get_db_runtime()).save(
        assistant_message_id=message_id,
        conversation_id=payload.conversation_id,
        owner_user_id=current_user.id,
        reaction=payload.feedback,
        reason=payload.reason,
        comment=payload.comment,
        what_went_wrong=payload.what_went_wrong,
        expected_answer=payload.expected_answer,
    )
    if saved is None or saved.message.feedback_updated_at is None:
        raise HTTPException(status_code=404, detail="Message not found")

    if saved.queued_for_langfuse:
        try:
            _task_queue().send_task("human_review.deliver_langfuse")
        except Exception:
            logger.exception("human_review.langfuse_delivery_enqueue_failed", extra={"message_id": message_id})

    # Dispatch to appropriate handlers based on routing decision
    if FeedbackRoutingAction.RECORD_TO_RAG in routing_decision.actions:
        _dispatch_rag_feedback_pipeline(
            RagPipelineTaskRequest(
                workspace_id=payload.workspace_id,
                conversation_id=payload.conversation_id,
                message_id=message_id,
            )
        )

    if FeedbackRoutingAction.RECORD_NEGATIVE_METADATA_SIGNAL in routing_decision.actions:
        # Apply metadata signal if we have the service available
        try:
            # TODO: Apply metadata signal for negative feedback
            # db_runtime = get_db_runtime()
            # usage_store = PostgresMetadataUsageStore(db=db_runtime)
            # metadata_ranking = MetadataRankingService(usage_store=usage_store)
            pipeline_service.apply_metadata_signal(
                decision=routing_decision,
                workspace_id=payload.workspace_id,
                connector_name=message.selected_connector,
            )
        except Exception:
            logger.warning(
                "feedback.metadata_signal.failed",
                extra={"message_id": message_id},
            )

    return MessageFeedbackResponse(
        message_id=saved.message.id,
        conversation_id=saved.message.conversation_id,
        feedback=payload.feedback,
        feedback_updated_at=saved.message.feedback_updated_at.isoformat(),
        reason=payload.reason,
        comment=payload.comment,
        what_went_wrong=payload.what_went_wrong,
        expected_answer=payload.expected_answer,
    )


@router.post("/message/{message_id}/repair-turn", response_model=RepairTurnResponse)
def create_repair_turn(message_id: str, payload: RepairTurnRequest) -> RepairTurnResponse:
    """Generate a repair question based on negative feedback to fix the answer.

    This endpoint constructs a modified question that incorporates the negative
    feedback context, allowing the orchestrator to generate a corrected answer.
    """
    current_user = resolve_current_user(payload.workspace_id)
    chat_repo = ChatRepository(db=get_db_runtime())

    # Verify conversation ownership
    conversation = chat_repo.get_conversation(
        payload.conversation_id,
        owner_user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.workspace_id != payload.workspace_id:
        raise HTTPException(status_code=403, detail="Conversation does not belong to workspace")

    # Verify message exists and is an assistant message
    message = chat_repo.get_message(
        payload.conversation_id,
        message_id,
        owner_user_id=current_user.id,
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.role != "assistant":
        raise HTTPException(
            status_code=400, detail="Repair turn is allowed only for assistant messages"
        )

    # Feedback is owned by the persisted assistant message, never by a stale
    # browser payload. This also lets the compact thumbs-down UI use the new
    # expanded fields without restoring the old reason/comment controls.
    saved_reason = (
        FeedbackReason(message.feedback_reason)
        if message.feedback_reason in {item.value for item in FeedbackReason}
        else FeedbackReason.OTHER
    )
    payload = payload.model_copy(
        update={
            "feedback_reason": saved_reason,
            "feedback_comment": message.feedback_comment,
            "what_went_wrong": message.feedback_what_went_wrong,
            "expected_answer": message.feedback_expected_answer,
        }
    )

    # Build repair question
    repair_service = RepairTurnService(logger=logger)
    repair_question = repair_service.build_repair_question(payload)

    logger.info(
        "repair_turn.created",
        extra=repair_service.extract_repair_context(payload),
    )

    return RepairTurnResponse(
        repair_question=repair_question,
        original_conversation_id=payload.conversation_id,
        original_message_id=payload.message_id,
    )
