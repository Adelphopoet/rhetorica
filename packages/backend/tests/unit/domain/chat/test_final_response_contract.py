"""Tests for strict final response contract."""

from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    SUBMIT_FINAL_ANSWER_TOOL_NAME,
    final_response_json_schema,
    final_response_tool_schema,
    validate_final_response_payload,
)

from agents.strict_schema import ensure_strict_json_schema


def test_validate_payload_accepts_minimal_answer() -> None:
    payload = {"answer_markdown": "OK"}

    validated, errors = validate_final_response_payload(payload)

    assert errors == []
    assert validated is not None
    assert validated.answer_markdown == "OK"
    assert validated.to_payload()["claims"] == []
    assert validated.to_payload()["evidence"] == []


def test_validate_payload_rejects_legacy_answer_alias() -> None:
    payload = {"answer": "OK"}

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert len(errors) >= 1


def test_validate_payload_rejects_missing_answer() -> None:
    payload = {"uncertainty_flags": []}

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert len(errors) >= 1


def test_validate_payload_rejects_blank_answer() -> None:
    payload = {"answer_markdown": "   "}

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert len(errors) >= 1


def test_schema_contains_required_fields() -> None:
    schema = final_response_json_schema()

    assert "properties" in schema
    assert "answer_markdown" in schema["properties"]
    assert "claims" not in schema["properties"]
    assert "evidence" not in schema["properties"]


def test_schema_is_compatible_with_agents_sdk_strict_tools() -> None:
    schema = final_response_json_schema()

    strict_schema = ensure_strict_json_schema(schema)

    assert strict_schema["additionalProperties"] is False
    clarification_choice = strict_schema["$defs"]["ClarificationChoice"]
    assert clarification_choice["additionalProperties"] is False
    assert "metadata" not in clarification_choice["properties"]


def test_submit_final_answer_tool_uses_contract_schema() -> None:
    tool = final_response_tool_schema()

    assert tool["type"] == "function"
    assert tool["function"]["name"] == SUBMIT_FINAL_ANSWER_TOOL_NAME
    assert tool["function"]["strict"] is True
    assert "progress update" in tool["function"]["description"]
    assert "For a simple reply" in tool["function"]["description"]
    assert tool["function"]["parameters"]["title"] == "ChatFinalResponseContractV1"
    assert "answer_markdown" in tool["function"]["parameters"]["properties"]


def test_validate_payload_rejects_backend_owned_evidence_fields() -> None:
    payload = {
        "answer_markdown": "Документация найдена.",
        "evidence": [],
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert any("Extra inputs are not permitted" in error for error in errors)


def test_validate_payload_accepts_valid_visualization_spec() -> None:
    payload = {
        "answer_markdown": "Показал график.",
        "uncertainty_flags": [],
        "visualization": {
            "version": "v2",
            "kind": "line",
            "data_source": {
                "execution_id": "exec-1",
                "row_count": 5,
                "is_truncated": False,
            },
            "encoding": {
                "x": "report_date",
                "y": ["orders_count"],
            },
            "transform": {
                "limit": 50,
                "aggregation": "none",
                "normalize": "none",
                "cumulative": False,
            },
            "rationale": {
                "why_kind": "Линейный график подходит для дневного тренда.",
                "why_fields": ["report_date", "orders_count"],
            },
        },
    }

    validated, errors = validate_final_response_payload(payload)

    assert errors == []
    assert validated is not None
    assert validated.to_payload()["visualization"]["kind"] == "line"


def test_validate_payload_rejects_visualization_json_inside_answer_markdown() -> None:
    payload = {
        "answer_markdown": "\n".join([
            "Показал график.",
            "",
            "```json",
            '{"visualization":{"version":"v2","kind":"bar","data_source":{"execution_id":"exec-1"},"encoding":{"x":"day","y":["orders"]}}}',
            "```",
        ]),
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert any("visualization JSON" in error for error in errors)


def test_validate_payload_rejects_raw_visualization_spec_inside_answer_markdown() -> None:
    payload = {
        "answer_markdown": "\n".join([
            "```json",
            '{"version":"v2","kind":"bar","data_source":{"execution_id":"exec-1"},"encoding":{"x":"day","y":["orders"]}}',
            "```",
        ]),
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert any("visualization JSON" in error for error in errors)


def test_validate_payload_rejects_incomplete_visualization_spec() -> None:
    payload = {
        "answer_markdown": "Показал график.",
        "uncertainty_flags": [],
        "visualization": {"kind": "line"},
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert any(error.startswith("visualization.") for error in errors)
