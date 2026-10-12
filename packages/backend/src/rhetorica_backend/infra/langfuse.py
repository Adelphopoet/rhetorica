"""Opt-in Langfuse tracing for agent turns without leaking chat content by default."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from rhetorica_backend.infra.logging import get_logger

if TYPE_CHECKING:
    from rhetorica_backend.contracts.chat import ChatAskRequest

logger = get_logger(__name__)


@dataclass(slots=True)
class LangfuseRuntime:
    """Own the isolated provider used exclusively by OpenAI Agents instrumentation."""

    enabled: bool
    client: object | None = None
    _closed: bool = False

    @classmethod
    def disabled(cls) -> LangfuseRuntime:
        return cls(enabled=False)

    def shutdown(self) -> None:
        if not self.enabled or self._closed or self.client is None:
            return
        self._closed = True
        try:
            self.client.shutdown()  # type: ignore[union-attr]
        except Exception:
            logger.exception("langfuse.shutdown_failed")
        finally:
            self.enabled = False


@dataclass(slots=True)
class LangfuseAgentTurn:
    """Expose the stable root trace identity without leaking the Langfuse SDK."""

    trace_id: str | None
    _observation: object | None = None
    _capture_content: bool = False

    def record_final_output(self, answer_markdown: str) -> None:
        """Attach the user-visible answer only when content export was explicitly enabled."""

        if not self._capture_content or self._observation is None:
            return
        update = getattr(self._observation, "update", None)
        if not callable(update):
            logger.error("langfuse.root_output_unavailable")
            return
        try:
            update(output=answer_markdown)
        except Exception:
            logger.exception("langfuse.root_output_update_failed")


_runtime = LangfuseRuntime.disabled()
_trace_id: ContextVar[str | None] = ContextVar("rhetorica_langfuse_trace_id", default=None)


def content_minimizing_trace_config(*, capture_content: bool):
    """Return the OpenInference policy used by the real instrumentation."""
    from openinference.instrumentation import TraceConfig

    return TraceConfig(
        hide_inputs=not capture_content,
        hide_outputs=not capture_content,
        hide_input_messages=not capture_content,
        hide_output_messages=not capture_content,
        hide_input_text=not capture_content,
        hide_output_text=not capture_content,
        hide_llm_tools=not capture_content,
    )


def install_langfuse(
    *,
    enabled: bool,
    service_version: str,
    environment: str,
) -> LangfuseRuntime:
    """Install official OpenInference tracing, but fail open if it is unavailable."""

    global _runtime
    if not enabled:
        logger.info("langfuse.disabled", reason="configuration")
        _runtime = LangfuseRuntime.disabled()
        return _runtime

    if not _has_credentials():
        logger.error("langfuse.disabled", reason="credentials_missing")
        _runtime = LangfuseRuntime.disabled()
        return _runtime

    client: object | None = None
    try:
        from langfuse import Langfuse
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

        tracer_provider = TracerProvider(
            sampler=ALWAYS_ON,
            resource=Resource.create(
                {
                    "service.name": "rhetorica-agent-runtime",
                    "service.namespace": "rhetorica",
                    "service.version": service_version,
                    "deployment.environment.name": environment,
                }
            ),
        )
        capture_content = langfuse_capture_content()
        client = Langfuse(
            tracer_provider=tracer_provider,
            environment=environment,
            release=service_version,
        )
        instrumentor = OpenAIAgentsInstrumentor()
        if instrumentor.is_instrumented_by_opentelemetry:
            logger.error("langfuse.disabled", reason="openinference_already_instrumented")
            client.shutdown()
            _runtime = LangfuseRuntime.disabled()
            return _runtime
        instrumentor.instrument(
            tracer_provider=tracer_provider,
            config=content_minimizing_trace_config(capture_content=capture_content),
            # Keep the Agents SDK callbacks while replacing its default OpenAI
            # exporter with the Langfuse-backed OpenInference processor.
            exclusive_processor=True,
        )
    except Exception:
        logger.exception("langfuse.disabled", reason="initialization_failed")
        shutdown = getattr(client, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logger.exception("langfuse.shutdown_failed")
        _runtime = LangfuseRuntime.disabled()
        return _runtime

    _runtime = LangfuseRuntime(enabled=True, client=client)
    logger.info(
        "langfuse.enabled",
        capture_content=capture_content,
        instrumentation="openinference.openai_agents",
    )
    return _runtime


def langfuse_enabled() -> bool:
    return _runtime.enabled


def langfuse_capture_content() -> bool:
    """Content export requires an explicit local opt-in because it may contain governed data."""

    return os.getenv("LANGFUSE_CAPTURE_CONTENT", "false").strip().lower() == "true"


def current_langfuse_trace_id() -> str | None:
    """Return the trace created for the active SDK turn, if tracing is enabled."""

    return _trace_id.get()


@contextmanager
def langfuse_agent_turn(
    request: ChatAskRequest, *, streamed: bool
) -> Iterator[LangfuseAgentTurn]:
    """Create one root trace with optional user-visible input and output."""

    if not _runtime.enabled:
        yield LangfuseAgentTurn(trace_id=None)
        return

    from langfuse import propagate_attributes

    capture_content = langfuse_capture_content()
    actor = request.actor_context
    metadata: dict[str, object] = {
        "workspace_id": request.workspace_id,
        "agent_id": request.agent_id,
        "llm_connector_name": request.llm_connector_name,
        "database_connector_name": request.database_connector_name,
        "run_sql": request.run_sql,
        "source": actor.source if actor is not None else None,
        "streamed": streamed,
    }

    with _runtime.client.start_as_current_observation(  # type: ignore[union-attr]
        as_type="agent",
        name="rhetorica.chat.turn",
        input=request.question if capture_content else None,
    ) as observation:
        span_context = otel_trace.get_current_span().get_span_context()
        trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
        token = _trace_id.set(trace_id)
        turn = LangfuseAgentTurn(
            trace_id=trace_id,
            _observation=observation,
            _capture_content=capture_content,
        )
        try:
            with propagate_attributes(
                trace_name="rhetorica.chat.turn",
                session_id=request.conversation_id,
                user_id=actor.external_user_id if actor is not None else None,
                metadata={key: value for key, value in metadata.items() if value is not None},
                tags=["rhetorica", "chat", "streamed" if streamed else "sync"],
            ):
                yield turn
        finally:
            _trace_id.reset(token)


def _has_credentials() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        and os.getenv("LANGFUSE_BASE_URL", "").strip()
    )


class LangfuseReviewPublisher:
    """Deliver one safe, idempotent feedback projection to Langfuse."""

    def __init__(self) -> None:
        self._enabled = os.getenv("LANGFUSE_ENABLED", "false").strip().lower() == "true"
        self._queue_id = os.getenv("LANGFUSE_ANNOTATION_QUEUE_ID", "").strip()
        self._feedback_score_name = os.getenv(
            "LANGFUSE_FEEDBACK_SCORE_NAME", "rhetorica_human_feedback"
        ).strip()
        self._priority_score_name = os.getenv(
            "LANGFUSE_REVIEW_PRIORITY_SCORE_NAME", "rhetorica_review_priority"
        ).strip()

    def publish(self, payload: dict[str, object]) -> bool:
        trace_id = payload.get("trace_id")
        if not self._enabled or not isinstance(trace_id, str) or not trace_id:
            return False
        if not self._queue_id:
            raise RuntimeError("langfuse_annotation_queue_id_missing")
        if not _has_credentials():
            raise RuntimeError("langfuse_credentials_missing")
        feedback_id = payload.get("score_id")
        reaction = payload.get("reaction")
        if not isinstance(feedback_id, str) or not isinstance(reaction, str):
            raise TypeError("langfuse_feedback_payload_invalid")
        comment = json.dumps(
            {
                "what_went_wrong": payload.get("what_went_wrong"),
                "expected_answer": payload.get("expected_answer"),
                "reason": payload.get("reason"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._create_score(
            score_id=feedback_id,
            trace_id=trace_id,
            name=self._feedback_score_name,
            value=reaction,
            data_type="CATEGORICAL",
            comment=comment,
        )
        priority = payload.get("priority")
        if isinstance(priority, str) and priority:
            self._create_score(
                score_id=f"{feedback_id}:priority",
                trace_id=trace_id,
                name=self._priority_score_name,
                value=priority,
                data_type="CATEGORICAL",
                comment=json.dumps(
                    payload.get("priority_evidence", {}),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        if payload.get("is_negative") is True and self._queue_id:
            self._add_to_annotation_queue(trace_id)
        return True

    def _create_score(
        self,
        *,
        score_id: str,
        trace_id: str,
        name: str,
        value: str,
        data_type: str,
        comment: str,
    ) -> None:
        """Synchronously persist one idempotent score before closing the outbox item.

        The SDK deliberately buffers score-create events. The review outbox needs
        a server-confirmed HTTP result instead: a successful response here means
        Langfuse accepted the score identified by its stable id.
        """

        base_url = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
        response = httpx.post(
            f"{base_url}/api/public/scores",
            headers=self._authorization_headers(),
            json={
                "id": score_id,
                "traceId": trace_id,
                "name": name,
                "value": value,
                "dataType": data_type,
                "comment": comment,
            },
            timeout=10.0,
        )
        response.raise_for_status()

    def _add_to_annotation_queue(self, trace_id: str) -> None:
        base_url = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
        response = httpx.post(
            f"{base_url}/api/public/annotation-queues/{self._queue_id}/items",
            headers=self._authorization_headers(),
            json={"objectType": "TRACE", "objectId": trace_id},
            timeout=10.0,
        )
        # A retry may repeat a successfully accepted object after a transport
        # timeout. The queue treats the existing item as the same work item.
        if response.status_code == 409:
            return
        response.raise_for_status()

    @staticmethod
    def _authorization_headers() -> dict[str, str]:
        public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
        secret_key = os.environ["LANGFUSE_SECRET_KEY"]
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
