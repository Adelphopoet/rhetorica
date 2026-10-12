"""Regression tests for Langfuse outbox delivery."""

from __future__ import annotations

from importlib import import_module

from rhetorica_backend.infra.postgres.langfuse_feedback_repository import ClaimedLangfuseFeedback

celery_runtime = import_module("rhetorica_worker.runtime.celery_app")


def test_langfuse_delivery_marks_exact_claim_after_success(monkeypatch) -> None:
    claim = ClaimedLangfuseFeedback(
        id="outbox-1",
        revision=3,
        claim_token="claim-1",
        payload={"trace_id": "a" * 32},
    )

    class Repository:
        def claim_pending_delivery(self, *, limit: int):
            assert limit == 100
            return [claim]

        def mark_delivery(self, **kwargs):
            assert kwargs == {
                "outbox_id": "outbox-1",
                "revision": 3,
                "claim_token": "claim-1",
                "error": None,
            }
            return True

    class Publisher:
        def publish(self, payload):
            assert payload == claim.payload
            return True

    monkeypatch.setattr(celery_runtime, "_execution_runtime", lambda: (None, object()))
    monkeypatch.setattr(celery_runtime, "LangfuseFeedbackOutboxRepository", lambda **_: Repository())
    monkeypatch.setattr(celery_runtime, "LangfuseReviewPublisher", Publisher)

    assert celery_runtime.deliver_human_review_to_langfuse() == {"delivered": 1, "failed": 0}


def test_langfuse_delivery_retries_when_publisher_is_disabled(monkeypatch) -> None:
    claim = ClaimedLangfuseFeedback(
        id="outbox-1", revision=1, claim_token="claim-1", payload={"trace_id": "a" * 32}
    )
    calls: list[dict[str, object]] = []

    class Repository:
        def claim_pending_delivery(self, *, limit: int):
            return [claim]

        def mark_delivery(self, **kwargs):
            calls.append(kwargs)
            return True

    class Publisher:
        def publish(self, payload):
            return False

    monkeypatch.setattr(celery_runtime, "_execution_runtime", lambda: (None, object()))
    monkeypatch.setattr(celery_runtime, "LangfuseFeedbackOutboxRepository", lambda **_: Repository())
    monkeypatch.setattr(celery_runtime, "LangfuseReviewPublisher", Publisher)

    assert celery_runtime.deliver_human_review_to_langfuse() == {"delivered": 0, "failed": 1}
    assert calls[0]["error"] == "publisher_not_enabled"
