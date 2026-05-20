from pydantic import BaseModel

from rhetorica_backend.infra.logging import (
    build_llm_request_log,
    build_llm_response_log,
    sanitize_for_logging,
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
