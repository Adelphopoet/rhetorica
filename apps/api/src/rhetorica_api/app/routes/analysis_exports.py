from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from celery import Celery  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.export import AnalysisExportFormat
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.domain.exports import AnalysisExportService
from rhetorica_backend.infra.celery_runtime import build_celery_app
from rhetorica_backend.infra.postgres.chat_repository import ChatMessageItem, ChatRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime

from rhetorica_api.app.identity import get_db_runtime, resolve_current_user
from rhetorica_api.app.settings import settings

router = APIRouter(prefix="/v1/chat", tags=["analysis-exports"])


@lru_cache(maxsize=1)
def _runtime() -> tuple[ExecutionService, DatabaseRuntime]:
    return build_execution_service(settings.database_url)


def _execution_service() -> ExecutionService:
    service, _ = _runtime()
    return service


@lru_cache(maxsize=1)
def _celery_app() -> Celery:
    return build_celery_app(
        broker_url=settings.celery_broker_url,
        result_backend=settings.celery_result_backend,
    )


def _result_payload(execution_id: str) -> ExecuteSqlResponse | None:
    task_result = _celery_app().AsyncResult(execution_id)
    if not task_result.ready() or not isinstance(task_result.result, dict):
        return None
    return ExecuteSqlResponse.model_validate(task_result.result)


def _find_message_pair(
    messages: list[ChatMessageItem],
    message_id: str,
) -> tuple[ChatMessageItem, str | None]:
    question: str | None = None
    assistant_message: ChatMessageItem | None = None
    for message in messages:
        if message.id == message_id:
            assistant_message = message
            break
        if message.role == "user":
            question = message.content
    if assistant_message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return assistant_message, question


def _content_disposition(filename: str) -> str:
    suffix = Path(filename).suffix or ".bin"
    fallback_stem = re.sub(r"[^a-z0-9_-]+", "-", Path(filename).stem.lower().encode("ascii", "ignore").decode("ascii"))
    fallback_stem = re.sub(r"-{2,}", "-", fallback_stem).strip("-_")
    fallback_name = f"{fallback_stem or 'analysis-export'}{suffix}"
    return (
        f'attachment; filename="{fallback_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


@router.get(
    "/conversation/{conversation_id}/message/{message_id}/export/{export_format}"
)
def export_analysis_message(
    conversation_id: str,
    message_id: str,
    export_format: AnalysisExportFormat,
) -> Response:
    chat_repo = ChatRepository(db=get_db_runtime())
    summary = chat_repo.get_conversation(conversation_id=conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    current_user = resolve_current_user(summary.workspace_id)
    conversation = chat_repo.get_conversation_with_messages(
        conversation_id=conversation_id,
        message_limit=200,
        owner_user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    assistant_message, question = _find_message_pair(conversation.messages, message_id)
    if assistant_message.role != "assistant":
        raise HTTPException(status_code=400, detail="Export is allowed only for assistant messages")

    execution_detail = None
    if assistant_message.execution_id:
        execution_detail = _execution_service().get_detail(
            execution_id=assistant_message.execution_id,
            result=_result_payload(assistant_message.execution_id),
        )

    export_service = AnalysisExportService()
    bundle = export_service.build_bundle(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        assistant_message=assistant_message,
        question=question,
        execution_detail=execution_detail,
    )
    rendered = export_service.render(bundle, export_format)
    return Response(
        content=rendered.content,
        media_type=rendered.content_type,
        headers={
            "Content-Disposition": _content_disposition(rendered.filename),
            "Cache-Control": "no-store",
        },
    )
