"""SSE streaming endpoint for chat."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from rhetorica_backend.contracts.chat import AnalystChatAskRequest, ChatAskRequest
from rhetorica_backend.infra.logging import emit_stdout_event, get_logger, sanitize_for_logging
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository

from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.identity import get_db_runtime, resolve_current_user

logger = get_logger(__name__)
SSE_KEEPALIVE_SECONDS = 15.0

router = APIRouter(prefix="/v1/chat", tags=["chat-streaming"])

_get_factory = get_chat_service_factory


async def _stream_event_generator(
    payload: ChatAskRequest,
    current_user_id: str,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from streaming orchestrator."""
    stream_logger = logger.bind(
        workspace_id=payload.workspace_id,
        conversation_id=payload.conversation_id,
    )
    factory = _get_factory()
    streaming_orchestrator = factory.create_streaming_orchestrator(
        current_user_id=current_user_id,
    )
    cancel_event = asyncio.Event()
    stream = streaming_orchestrator.stream_ask(payload, cancel_event=cancel_event)
    iterator = stream.__aiter__()
    next_event_task: asyncio.Task | None = asyncio.create_task(iterator.__anext__())

    try:
        while True:
            try:
                if next_event_task is None:
                    break
                event = await asyncio.wait_for(
                    asyncio.shield(next_event_task),
                    timeout=SSE_KEEPALIVE_SECONDS,
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break
            next_event_task = asyncio.create_task(iterator.__anext__())
            # Serialize event to JSON
            event_dict = event.model_dump(mode="json")
            # SSE format: event: {type}\ndata: {json}\n\n
            yield f"event: {event.type.value}\ndata: {json.dumps(event_dict, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        stream_logger.info("api.chat.stream.cancelled")
        cancel_event.set()
    except Exception as exc:
        stream_logger.exception("api.chat.stream.failed")
        error_event = {
            "type": "error",
            "code": "stream_error",
            "message": str(exc),
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    finally:
        cancel_event.set()
        if next_event_task is not None and not next_event_task.done():
            next_event_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_event_task
        await stream.aclose()


@router.post("/ask/stream")
async def ask_chat_stream(payload: AnalystChatAskRequest) -> StreamingResponse:
    """Stream chat response with SSE events.

    Events:
    - thinking: LLM reasoning blocks
    - tool_call_start: Tool execution started
    - tool_call_end: Tool execution completed
    - execution_status: SQL execution progress
    - chunk: LLM response chunk
    - done: Stream completed successfully
    - error: Stream failed
    """
    log_payload = {
        "workspace_id": payload.workspace_id,
        "conversation_id": payload.conversation_id,
        "run_sql": payload.run_sql,
        "question": sanitize_for_logging(payload.question, max_string_length=500),
    }
    logger.bind(**log_payload).info("api.chat.ask_stream.accepted")
    emit_stdout_event("api.chat.ask_stream.accepted", **log_payload)
    internal_request = ChatAskRequest.model_validate(payload.model_dump())
    current_user = resolve_current_user(payload.workspace_id)
    if internal_request.conversation_id is not None:
        chat_repo = ChatRepository(db=get_db_runtime())
        conversation = chat_repo.get_conversation(
            internal_request.conversation_id,
            owner_user_id=current_user.id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.workspace_id != internal_request.workspace_id:
            raise HTTPException(status_code=403, detail="Conversation does not belong to workspace")

    return StreamingResponse(
        _stream_event_generator(internal_request, current_user_id=current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
