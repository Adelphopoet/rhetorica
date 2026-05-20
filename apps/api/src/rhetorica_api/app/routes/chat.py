from functools import lru_cache

from celery import Celery  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from rhetorica_backend.contracts import (
    AnalysisRunActionResponse,
    AnalysisRunStateResponse,
    ConversationDetailItem,
    ConversationItem,
    CreateConversationRequest,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    RepairTurnRequest,
    RepairTurnResponse,
)
from rhetorica_backend.domain.chat.feedback import (
    FeedbackContext,
    FeedbackPipelineService,
    FeedbackPolicy,
    FeedbackRoutingAction,
    RepairTurnService,
)
from rhetorica_backend.domain.chat.orchestration.analysis_artifacts import (
    AnalysisCheckpoint,
    AnalysisCheckpointLifecycle,
    AnalysisPlan,
    AnalysisPlanLifecycle,
)
from rhetorica_backend.domain.rag import RagPipelineTaskRequest
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository

from rhetorica_api.app.identity import get_db_runtime, resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/chat", tags=["chat"])
logger = get_logger(__name__)


def _get_feedback_pipeline_service() -> FeedbackPipelineService:
    """Get the feedback pipeline service (created per-request for DI)."""
    policy = FeedbackPolicy(logger=logger)
    return FeedbackPipelineService(policy=policy, logger=logger)


def _runtime_context_from_message(msg) -> dict:
    trace = msg.capability_trace if isinstance(msg.capability_trace, dict) else {}
    runtime_context = trace.get("runtime_context")
    return runtime_context if isinstance(runtime_context, dict) else {}


def _analysis_plan_from_message(msg) -> dict | None:
    value = _runtime_context_from_message(msg).get("analysis_plan")
    return value if isinstance(value, dict) else None


def _worker_results_from_message(msg) -> list[dict]:
    value = _runtime_context_from_message(msg).get("worker_results")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _checkpoint_from_state(state: dict) -> dict | None:
    value = state.get("analysis_checkpoint")
    return value if isinstance(value, dict) else None


def _analysis_plan_from_state(state: dict) -> dict | None:
    value = state.get("analysis_plan")
    return value if isinstance(value, dict) else None


def _worker_results_from_state(state: dict) -> list[dict]:
    value = state.get("analysis_worker_results")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
    chat_repo = ChatRepository(db=get_db_runtime())
    conversation = chat_repo.create_conversation(
        workspace_id=payload.workspace_id,
        title=payload.title,
        owner_user_id=current_user.id,
    )
    return ConversationItem(
        id=conversation.id,
        workspace_id=conversation.workspace_id,
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        message_count=0,
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
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        messages=[
            {
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "role": msg.role,
                "content": msg.content,
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
                "analysis_plan": _analysis_plan_from_message(msg),
                "worker_results": _worker_results_from_message(msg),
                "checkpoint_digest": _runtime_context_from_message(msg).get(
                    "checkpoint_digest"
                ),
                "warnings": msg.warnings,
                "feedback": msg.feedback,
                "feedback_updated_at": msg.feedback_updated_at.isoformat()
                if msg.feedback_updated_at
                else None,
                "feedback_reason": msg.feedback_reason,
                "feedback_comment": msg.feedback_comment,
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
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=getattr(conv, "message_count", len(conv.messages)),
        )
        for conv in conversations
    ]


@router.delete("/conversation/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    """Delete a conversation and all its messages."""
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


@router.get(
    "/conversation/{conversation_id}/analysis-run",
    response_model=AnalysisRunStateResponse,
)
def get_analysis_run_state(conversation_id: str) -> AnalysisRunStateResponse:
    """Return compact durable state for an active analysis run."""
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
    checkpoint = _checkpoint_from_state(conversation.conversation_state)
    last_assistant = next(
        (message for message in reversed(conversation.messages) if message.role == "assistant"),
        None,
    )
    plan = _analysis_plan_from_state(conversation.conversation_state) or (
        _analysis_plan_from_message(last_assistant) if last_assistant else None
    )
    worker_results = _worker_results_from_state(conversation.conversation_state) or (
        _worker_results_from_message(last_assistant) if last_assistant else []
    )
    status = str((checkpoint or {}).get("status") or "planned")
    return AnalysisRunStateResponse(
        conversation_id=conversation_id,
        status=status,
        checkpoint_digest=checkpoint,
        analysis_plan=plan,
        worker_results=worker_results,
        resumable=status in {"planned", "running", "blocked", "awaiting_user", "failed"},
        cancellable=status in {"planned", "running", "blocked", "awaiting_user"},
    )


@router.post(
    "/conversation/{conversation_id}/analysis-run/resume",
    response_model=AnalysisRunActionResponse,
)
def resume_analysis_run(conversation_id: str) -> AnalysisRunActionResponse:
    """Mark a persisted blocked/failed run as resumable without replaying transcripts."""
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    current_user = resolve_current_user(summary.workspace_id)
    state = chat_repo.get_conversation_state(
        conversation_id=conversation_id,
        owner_user_id=current_user.id,
    )
    checkpoint_payload = _checkpoint_from_state(state)
    if checkpoint_payload is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    checkpoint = AnalysisCheckpoint.model_validate(checkpoint_payload)
    if checkpoint.status == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled analysis run cannot resume")
    if checkpoint.status == "completed":
        raise HTTPException(status_code=409, detail="Completed analysis run cannot resume")
    active_step_id = checkpoint.blocked_step_id or checkpoint.active_step_id
    resumed = checkpoint.model_copy(
        update={
            "status": "running",
            "active_step_id": active_step_id,
            "blocked_step_id": None,
            "blocker": None,
        }
    )
    state["analysis_checkpoint"] = resumed.digest_payload()
    raw_plan = _analysis_plan_from_state(state)
    if raw_plan is not None and active_step_id:
        plan = AnalysisPlan.model_validate(raw_plan)
        state["analysis_plan"] = AnalysisPlanLifecycle().update_step(
            plan=plan,
            step_id=active_step_id,
            status="planned",
            blocker=None,
        ).model_dump(mode="json")
    chat_repo.update_conversation_state(
        conversation_id=conversation_id,
        conversation_state=state,
        owner_user_id=current_user.id,
    )
    return AnalysisRunActionResponse(
        conversation_id=conversation_id,
        status=resumed.status,
        checkpoint_digest=resumed.digest_payload(),
    )


@router.post(
    "/conversation/{conversation_id}/analysis-run/cancel",
    response_model=AnalysisRunActionResponse,
)
def cancel_analysis_run(conversation_id: str) -> AnalysisRunActionResponse:
    """Cancel a persisted analysis run checkpoint."""
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    current_user = resolve_current_user(summary.workspace_id)
    state = chat_repo.get_conversation_state(
        conversation_id=conversation_id,
        owner_user_id=current_user.id,
    )
    checkpoint_payload = _checkpoint_from_state(state)
    if checkpoint_payload is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    checkpoint = AnalysisCheckpoint.model_validate(checkpoint_payload)
    cancelled = AnalysisCheckpointLifecycle().cancel(checkpoint=checkpoint)
    state["analysis_checkpoint"] = cancelled.digest_payload()
    step_id = checkpoint.active_step_id or checkpoint.blocked_step_id
    raw_plan = _analysis_plan_from_state(state)
    if raw_plan is not None and step_id:
        plan = AnalysisPlan.model_validate(raw_plan)
        state["analysis_plan"] = AnalysisPlanLifecycle().update_step(
            plan=plan,
            step_id=step_id,
            status="cancelled",
            blocker="cancelled_by_user",
        ).model_dump(mode="json")
    chat_repo.update_conversation_state(
        conversation_id=conversation_id,
        conversation_state=state,
        owner_user_id=current_user.id,
    )
    return AnalysisRunActionResponse(
        conversation_id=conversation_id,
        status=cancelled.status,
        checkpoint_digest=cancelled.digest_payload(),
    )


@router.post(
    "/conversation/{conversation_id}/analysis-run/approve",
    response_model=AnalysisRunActionResponse,
)
def approve_analysis_run(conversation_id: str) -> AnalysisRunActionResponse:
    """Approve a pending SQL execution gate for an analysis run."""
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    current_user = resolve_current_user(summary.workspace_id)
    state = chat_repo.get_conversation_state(
        conversation_id=conversation_id,
        owner_user_id=current_user.id,
    )
    checkpoint_payload = _checkpoint_from_state(state)
    if checkpoint_payload is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    checkpoint = AnalysisCheckpoint.model_validate(checkpoint_payload)
    if checkpoint.status != "awaiting_user":
        raise HTTPException(status_code=409, detail="Analysis run is not awaiting approval")
    if not isinstance(checkpoint.budget_state.get("pending_approval"), dict):
        raise HTTPException(status_code=409, detail="No pending approval found")
    approved = AnalysisCheckpointLifecycle().approve(checkpoint=checkpoint)
    state["analysis_checkpoint"] = approved.digest_payload()
    raw_plan = _analysis_plan_from_state(state)
    active_step_id = approved.active_step_id
    if raw_plan is not None and active_step_id:
        plan = AnalysisPlan.model_validate(raw_plan)
        state["analysis_plan"] = AnalysisPlanLifecycle().update_step(
            plan=plan,
            step_id=active_step_id,
            status="planned",
            blocker=None,
        ).model_dump(mode="json")
    chat_repo.update_conversation_state(
        conversation_id=conversation_id,
        conversation_state=state,
        owner_user_id=current_user.id,
    )
    return AnalysisRunActionResponse(
        conversation_id=conversation_id,
        status=approved.status,
        checkpoint_digest=approved.digest_payload(),
    )


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

    # Update message feedback in database with reason and comment
    updated = chat_repo.update_message_feedback(
        conversation_id=payload.conversation_id,
        message_id=message_id,
        feedback=payload.feedback.value,
        owner_user_id=current_user.id,
        feedback_reason=payload.reason.value if payload.reason else None,
        feedback_comment=payload.comment,
    )
    if updated is None or updated.feedback_updated_at is None:
        raise HTTPException(status_code=404, detail="Message not found")

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
        message_id=updated.id,
        conversation_id=updated.conversation_id,
        feedback=payload.feedback,
        feedback_updated_at=updated.feedback_updated_at.isoformat(),
        reason=payload.reason,
        comment=payload.comment,
    )


@router.post("/message/{message_id}/repair-turn", response_model=RepairTurnResponse)
def create_repair_turn(
    message_id: str, payload: RepairTurnRequest
) -> RepairTurnResponse:
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
