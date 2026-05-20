"""Tests for strict final response contract."""

from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    final_response_json_schema,
    validate_final_response_payload,
)


def test_validate_payload_accepts_legacy_answer_alias() -> None:
    payload = {
        "answer": "OK",
        "sql": None,
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert errors == []
    assert validated is not None
    assert validated.answer_markdown == "OK"


def test_validate_payload_rejects_missing_answer() -> None:
    payload = {
        "sql": None,
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert len(errors) >= 1


def test_validate_payload_rejects_blank_answer() -> None:
    payload = {
        "answer_markdown": "   ",
        "sql": None,
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "uncertainty_flags": [],
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert len(errors) >= 1


def test_schema_contains_required_fields() -> None:
    schema = final_response_json_schema()

    assert "properties" in schema
    assert "answer_markdown" in schema["properties"]
    assert "claims" in schema["properties"]
    assert "evidence" in schema["properties"]


def test_validate_payload_accepts_valid_visualization_spec() -> None:
    payload = {
        "answer_markdown": "Показал график.",
        "sql": None,
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
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


def test_validate_payload_rejects_incomplete_visualization_spec() -> None:
    payload = {
        "answer_markdown": "Показал график.",
        "sql": None,
        "target": None,
        "claims": [],
        "evidence": [],
        "claim_evidence_links": [],
        "uncertainty_flags": [],
        "visualization": {"kind": "line"},
    }

    validated, errors = validate_final_response_payload(payload)

    assert validated is None
    assert any(error.startswith("visualization.") for error in errors)
