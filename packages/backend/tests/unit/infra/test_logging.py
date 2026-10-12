import logging

import rhetorica_backend.infra.logging as logging_module
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from pydantic import BaseModel
from rhetorica_backend.infra.logging import (
    build_llm_request_log,
    build_llm_response_log,
    configure_logging,
    llm_payload_contains_file_tools,
    sanitize_for_logging,
    sanitize_tool_arguments_for_logging,
)

REDACTED = "[REDACTED]"


class _LogPayload(BaseModel):
    code: str
    message: str


def test_sanitize_for_logging_redacts_sensitive_fields() -> None:
    payload = sanitize_for_logging(
        {
            "authorization": "Bearer secret",
            "nested": {"api_key": "top-secret"},
            "password": "123",
        }
    )

    assert payload["authorization"] == REDACTED
    assert payload["nested"]["api_key"] == REDACTED
    assert payload["password"] == REDACTED


def test_sanitize_for_logging_serializes_pydantic_models() -> None:
    payload = sanitize_for_logging(
        {"errors": [_LogPayload(code="missing_column", message="bad column")]}
    )

    assert payload == {
        "errors": [{"code": "missing_column", "message": "bad column"}]
    }


def test_file_tool_logging_never_contains_python_code() -> None:
    code = "from pathlib import Path\nPath('/workspace/output/result.csv').write_text('x')"

    sanitized = sanitize_tool_arguments_for_logging(
        "run_file_code",
        {"artifact_ids": ["artifact-1"], "code": code},
    )

    assert sanitized == {"artifact_ids": ["artifact-1"], "code_chars": len(code)}
    assert code not in str(sanitized)


def test_detects_file_tools_in_chat_and_responses_payloads() -> None:
    assert llm_payload_contains_file_tools({
        "tools": [{"type": "function", "function": {"name": "run_file_code"}}]
    })
    assert llm_payload_contains_file_tools({
        "tools": [{"type": "function", "name": "inspect_file"}]
    })
    assert not llm_payload_contains_file_tools({
        "tools": [{"type": "function", "function": {"name": "run_sql"}}]
    })


def test_json_serializer_handles_non_serializable_logging_extras() -> None:
    payload = logging_module._json_serializer({"protocol": object()})

    assert payload == '{"protocol":"<object>"}'


def test_build_llm_request_log_uses_summary_when_content_disabled() -> None:
    log_payload = build_llm_request_log(
        endpoint="https://llm.example.com/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        payload={
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "hello world"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "run_sql", "arguments": '{"sql":"select 1"}'},
                }
            ],
        },
        include_content=False,
    )

    assert "payload" not in log_payload
    assert log_payload["headers"]["Authorization"] == REDACTED
    assert log_payload["payload_summary"]["messages"][0]["content_chars"] == 11
    assert log_payload["payload_summary"]["tools"][0]["name"] == "run_sql"


def test_build_llm_response_log_keeps_raw_body_in_dev_mode() -> None:
    log_payload = build_llm_response_log(
        body={
            "id": "resp-1",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        },
        include_content=True,
    )

    assert log_payload["raw_body"]["choices"][0]["message"]["content"] == "done"


def test_configure_logging_keeps_sqlalchemy_quiet_in_dev(monkeypatch) -> None:
    monkeypatch.setattr(logging_module, "_LOGGING_CONFIGURED", False)

    configure_logging(
        service_name="test",
        environment="dev",
        log_level="DEBUG",
        json_logs=True,
    )

    assert logging.getLogger("sqlalchemy").level == logging.WARNING
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
    assert logging.getLogger("sqlalchemy.pool").level == logging.WARNING
    assert logging.getLogger("sqlalchemy.orm").level == logging.WARNING


def test_trace_context_fields_use_fixed_width_hex_ids() -> None:
    span = NonRecordingSpan(
        SpanContext(
            trace_id=1,
            span_id=2,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )

    with trace.use_span(span):
        fields = logging_module._trace_context_fields()

    assert fields == {
        "trace_id": "00000000000000000000000000000001",
        "span_id": "0000000000000002",
    }
