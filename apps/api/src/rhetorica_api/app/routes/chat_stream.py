"""SSE streaming endpoint for chat."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from rhetorica_backend.contracts.chat import (
    AnalystChatAskRequest,
    ChatActorContext,
    ChatAskRequest,
    DoneEvent,
    ErrorEvent,
)
from rhetorica_backend.domain.files.policy import ArtifactRejectedError
from rhetorica_backend.domain.files.service import ArtifactConflictError, ArtifactNotFoundError
from rhetorica_backend.infra.logging import emit_stdout_event, get_logger, sanitize_for_logging
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository

from rhetorica_api.app.agent_runtime import resolve_agent_for_chat
from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.file_runtime import get_artifact_service
from rhetorica_api.app.identity import get_db_runtime, resolve_current_user

logger = get_logger(__name__)
SSE_KEEPALIVE_SECONDS = 15.0

router = APIRouter(prefix="/v1/chat", tags=["chat-streaming"])

_get_factory = get_chat_service_factory


@dataclass(slots=True)
class _ActiveChatRun:
    cancel_event: asyncio.Event
    current_user_id: str
    workspace_id: str


_active_chat_runs: dict[str, _ActiveChatRun] = {}


async def _coding_event_generator(*, planner, payload, user, run_id):
    cancel = asyncio.Event()
    active = _ActiveChatRun(
        cancel_event=cancel, current_user_id=user.id, workspace_id=user.workspace_id
    )
    _active_chat_runs[run_id] = active
    stream = planner.stream(request=payload, user=user)
    next_event = asyncio.create_task(anext(stream))
    cancelled = asyncio.create_task(cancel.wait())
    try:
        while True:
            ready, _ = await asyncio.wait(
                [next_event, cancelled],
                timeout=SSE_KEEPALIVE_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in ready:
                event = DoneEvent(
                    answer="Обсуждение остановлено. Разработка не запускалась.",
                    conversation_id=payload.conversation_id,
                    cancelled=True,
                )
                yield f"event: done\ndata: {event.model_dump_json()}\n\n"
                return
            if not ready:
                yield ": ping\n\n"
                continue
            try:
                event = next_event.result()
            except StopAsyncIteration:
                return
            yield f"event: {event.type.value}\ndata: {event.model_dump_json()}\n\n"
            next_event = asyncio.create_task(anext(stream))
    except Exception as exc:
        from rhetorica_backend.domain.coding.errors import CodingRunError

        code = exc.code if isinstance(exc, CodingRunError) else "coding_planning_failed"
        # Upstream exceptions may contain credentials or repository bodies.
        logger.error(  # noqa: TRY400 — deliberately exclude unsafe upstream exception bodies
            "api.coding.planning.failed",
            workspace_id=user.workspace_id,
            conversation_id=payload.conversation_id,
            run_id=run_id,
            error_code=code,
            error_type=type(exc).__name__,
        )
        messages = {
            "coding_planning_iteration_limit_reached": (
                "Исчерпан обычный лимит итераций чата при подготовке плана. "
                "Разработка не запускалась; её бюджет не потрачен."
            ),
            "coding_planning_timeout": (
                "Модель не ответила за отведённое время при подготовке плана. "
                "Разработка не запускалась; повтори сообщение."
            ),
        }
        event = ErrorEvent(
            code=code,
            message=messages.get(
                code,
                "Не удалось подготовить план. Разработка не запускалась; повтори сообщение.",
            ),
        )
        yield f"event: error\ndata: {event.model_dump_json()}\n\n"
    finally:
        for pending in [next_event, cancelled]:
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await pending
        await stream.aclose()
        if _active_chat_runs.get(run_id) is active:
            _active_chat_runs.pop(run_id, None)


async def _stream_event_generator(
    payload: ChatAskRequest,
    current_user_id: str,
    http_request: Request | None = None,
    run_id: str | None = None,
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
    resolved_run_id = run_id or str(uuid4())
    active_run = _ActiveChatRun(
        cancel_event=cancel_event,
        current_user_id=current_user_id,
        workspace_id=payload.workspace_id,
    )
    _active_chat_runs[resolved_run_id] = active_run
    stream = streaming_orchestrator.stream_ask(payload, cancel_event=cancel_event)
    iterator = stream.__aiter__()
    next_event_task: asyncio.Future[Any] | None = asyncio.ensure_future(iterator.__anext__())

    try:
        while True:
            try:
                if next_event_task is None:
                    break
                if http_request is not None and await http_request.is_disconnected():
                    stream_logger.info("api.chat.stream.client_disconnected")
                    break
                event = await asyncio.wait_for(
                    asyncio.shield(next_event_task),
                    timeout=SSE_KEEPALIVE_SECONDS,
                )
            except TimeoutError:
                if http_request is not None and await http_request.is_disconnected():
                    stream_logger.info("api.chat.stream.client_disconnected")
                    break
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                break
            next_event_task = asyncio.ensure_future(iterator.__anext__())
            # Serialize event to JSON
            event_dict = event.model_dump(mode="json")
            # SSE format: event: {type}\ndata: {json}\n\n
            yield f"event: {event.type.value}\ndata: {json.dumps(event_dict, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        stream_logger.info("api.chat.stream.transport_disconnected")
        raise
    except Exception as exc:
        stream_logger.exception("api.chat.stream.failed")
        cancel_event.set()
        error_event = {
            "type": "error",
            "code": "stream_error",
            "message": str(exc),
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    finally:
        if next_event_task is not None and not next_event_task.done():
            next_event_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_event_task
        await stream.aclose()
        if _active_chat_runs.get(resolved_run_id) is active_run:
            _active_chat_runs.pop(resolved_run_id, None)


@router.post("/ask/stream")
async def ask_chat_stream(payload: AnalystChatAskRequest, request: Request) -> StreamingResponse:
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
        "attachment_count": len(payload.attachment_ids),
        "question": sanitize_for_logging(payload.question, max_string_length=500),
    }
    logger.bind(**log_payload).info("api.chat.ask_stream.accepted")
    emit_stdout_event("api.chat.ask_stream.accepted", **log_payload)
    internal_request = ChatAskRequest.model_validate(payload.model_dump())
    raw_run_id = request.headers.get("x-rhetorica-run-id")
    if raw_run_id is not None:
        try:
            run_id = str(UUID(raw_run_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_chat_run_id") from exc
    else:
        run_id = str(uuid4())
    current_user = resolve_current_user(payload.workspace_id)
    if internal_request.coding_mode or internal_request.coding_resources is not None:
        from .coding_runs import actor

        actor()
        if not internal_request.coding_mode:
            raise HTTPException(status_code=422, detail="coding_mode_required")
    internal_request = internal_request.model_copy(
        update={
            "actor_context": ChatActorContext(
                source="web",
                login=current_user.login,
                display_name=current_user.display_name,
                role=current_user.role,
                auth_provider=current_user.auth_provider,
                workspace_id=current_user.workspace_id,
            )
        }
    )
    db_runtime = get_db_runtime()
    existing_conversation = None
    if internal_request.conversation_id is not None:
        chat_repo = ChatRepository(db=db_runtime)
        existing_conversation = chat_repo.get_conversation(
            internal_request.conversation_id,
            owner_user_id=current_user.id,
        )
        if existing_conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if existing_conversation.workspace_id != internal_request.workspace_id:
            raise HTTPException(status_code=403, detail="Conversation does not belong to workspace")
    if internal_request.attachment_ids:
        if internal_request.conversation_id is None:
            raise HTTPException(
                status_code=422, detail="conversation_id is required for attachments"
            )
        try:
            get_artifact_service().validate_turn_inputs(
                artifact_ids=internal_request.attachment_ids,
                workspace_id=internal_request.workspace_id,
                conversation_id=internal_request.conversation_id,
                owner_user_id=current_user.id,
            )
        except ArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment not found") from exc
        except (ArtifactConflictError, ArtifactRejectedError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    internal_request, _agent = resolve_agent_for_chat(
        request=internal_request,
        agent_repository=AgentProfileRepository(db=db_runtime),
        current_user_id=current_user.id,
        existing_conversation=existing_conversation,
    )

    if internal_request.coding_mode:
        from .coding_runs import actor, invoke, planning_service

        user = actor()
        planner = planning_service()
        invoke(planner.selection, internal_request, user)

        return StreamingResponse(
            _coding_event_generator(
                planner=planner, payload=internal_request, user=user, run_id=run_id
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return StreamingResponse(
        _stream_event_generator(
            internal_request,
            current_user_id=current_user.id,
            http_request=request,
            run_id=run_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_chat_run(run_id: str, workspace_id: str = "default") -> dict[str, bool]:
    """Cancel an active SDK run only for an explicit user action."""
    try:
        normalized_run_id = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_chat_run_id") from exc
    current_user = resolve_current_user(workspace_id)
    active_run = _active_chat_runs.get(normalized_run_id)
    if active_run is None:
        return {"cancelled": False}
    if active_run.current_user_id != current_user.id or active_run.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="chat_run_not_found")
    active_run.cancel_event.set()
    return {"cancelled": True}
