import pytest
from rhetorica_backend.infra.langfuse import LangfuseReviewPublisher


class _Response:
    def __init__(self, status_code: int = 201, error: Exception | None = None) -> None:
        self.status_code = status_code
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


def _configure_langfuse(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.test")
    monkeypatch.setenv("LANGFUSE_ANNOTATION_QUEUE_ID", "queue-1")


def _negative_feedback_payload() -> dict[str, object]:
    return {
        "score_id": "rhetorica-feedback-message-1",
        "trace_id": "a" * 32,
        "conversation_id": "conversation-1",
        "reaction": "wrong",
        "reason": "wrong_metric",
        "is_negative": True,
        "what_went_wrong": "Metric uses the wrong period",
        "expected_answer": "Use the fiscal calendar",
        "priority": "high",
        "priority_evidence": {
            "conversation_message_count": 8,
            "tool_call_count": 4,
            "tool_error_count": 1,
            "policy_version": "v1",
        },
        # Forbidden execution content is deliberately ignored by the serializer.
        "question": "confidential question",
        "answer": "confidential answer",
        "sql": "select secret",
        "tool_arguments": {"password": "secret"},
        "tool_results": {"rows": [["secret"]]},
    }


def test_review_publisher_keeps_outbox_event_pending_when_langfuse_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")

    assert LangfuseReviewPublisher().publish({"trace_id": "a" * 32}) is False


def test_review_publisher_waits_for_each_score_http_confirmation(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    _configure_langfuse(monkeypatch)

    def post(url: str, **kwargs: object) -> _Response:
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr("rhetorica_backend.infra.langfuse.httpx.post", post)

    assert LangfuseReviewPublisher().publish(_negative_feedback_payload()) is True

    assert calls == [
        (
            "https://langfuse.test/api/public/scores",
            {
                "headers": {"Authorization": "Basic cGstdGVzdDpzay10ZXN0"},
                "json": {
                    "id": "rhetorica-feedback-message-1",
                    "traceId": "a" * 32,
                    "name": "rhetorica_human_feedback",
                    "value": "wrong",
                    "dataType": "CATEGORICAL",
                    "comment": '{"what_went_wrong":"Metric uses the wrong period","expected_answer":"Use the fiscal calendar","reason":"wrong_metric"}',
                },
                "timeout": 10.0,
            },
        ),
        (
            "https://langfuse.test/api/public/scores",
            {
                "headers": {"Authorization": "Basic cGstdGVzdDpzay10ZXN0"},
                "json": {
                    "id": "rhetorica-feedback-message-1:priority",
                    "traceId": "a" * 32,
                    "name": "rhetorica_review_priority",
                    "value": "high",
                    "dataType": "CATEGORICAL",
                    "comment": '{"conversation_message_count":8,"tool_call_count":4,"tool_error_count":1,"policy_version":"v1"}',
                },
                "timeout": 10.0,
            },
        ),
        (
            "https://langfuse.test/api/public/annotation-queues/queue-1/items",
            {
                "headers": {"Authorization": "Basic cGstdGVzdDpzay10ZXN0"},
                "json": {"objectType": "TRACE", "objectId": "a" * 32},
                "timeout": 10.0,
            },
        ),
    ]


def test_review_publisher_does_not_close_outbox_when_score_is_not_confirmed(monkeypatch) -> None:
    calls: list[str] = []
    _configure_langfuse(monkeypatch)

    def post(url: str, **_: object) -> _Response:
        calls.append(url)
        return _Response(error=RuntimeError("score delivery failed"))

    monkeypatch.setattr("rhetorica_backend.infra.langfuse.httpx.post", post)

    with pytest.raises(RuntimeError, match="score delivery failed"):
        LangfuseReviewPublisher().publish(_negative_feedback_payload())

    assert calls == ["https://langfuse.test/api/public/scores"]


def test_review_publisher_treats_annotation_queue_conflict_as_success(monkeypatch) -> None:
    _configure_langfuse(monkeypatch)

    def post(url: str, **_: object) -> _Response:
        return _Response(status_code=409) if url.endswith("/items") else _Response()

    monkeypatch.setattr("rhetorica_backend.infra.langfuse.httpx.post", post)

    assert LangfuseReviewPublisher().publish(
        {
            "score_id": "score-1",
            "trace_id": "a" * 32,
            "reaction": "wrong",
            "is_negative": True,
        }
    ) is True
