from __future__ import annotations

from importlib import import_module

from rhetorica_backend.domain.rag.models import RagPipelineResult

celery_runtime = import_module("rhetorica_worker.runtime.celery_app")


def test_sql_execute_task_uses_precreated_execution_id(monkeypatch) -> None:
    monkeypatch.setattr(
        celery_runtime,
        "_execution_runtime",
        lambda: (_FakeExecutionService(), None),
    )

    result = celery_runtime.sql_execute_run(
        "exec-1",
        {
            "sql": "SELECT 1",
            "connection": {
                "engine": "clickhouse",
                "host": "localhost",
                "port": 8123,
                "username": "default",
                "database": "default",
            },
        },
    )
    assert result["execution_id"] == "exec-1"


class _FakeExecutionService:
    def execute_prepared(self, execution_id, request):
        assert execution_id == "exec-1"
        assert request.sql == "SELECT 1"

        class _Response:
            def model_dump(self, mode="json"):
                _ = mode
                return {
                    "execution_id": execution_id,
                    "status": "succeeded",
                    "engine": request.connection.engine,
                    "preview": {"columns": ["value"], "rows": [[1]]},
                }

        return _Response()


def test_rag_pipeline_task_returns_service_result(monkeypatch) -> None:
    class _FakeService:
        def process_feedback(self, request):
            assert request.workspace_id == "default"
            return RagPipelineResult(status="stored", reason="ok", point_id="p1")

    monkeypatch.setattr(celery_runtime, "_rag_pipeline_service", lambda: _FakeService())

    result = celery_runtime.rag_insights_process_feedback(
        {
            "workspace_id": "default",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
        }
    )
    assert result["status"] == "stored"
    assert result["point_id"] == "p1"


def test_rag_pipeline_task_handles_pipeline_error(monkeypatch) -> None:
    class _BrokenService:
        def process_feedback(self, request):
            raise RuntimeError("boom")

    monkeypatch.setattr(celery_runtime, "_rag_pipeline_service", lambda: _BrokenService())

    result = celery_runtime.rag_insights_process_feedback(
        {
            "workspace_id": "default",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
        }
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "pipeline_error"
