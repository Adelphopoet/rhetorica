"""Strict final response contract for LLM chat runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from rhetorica_backend.contracts.chat import (
    AnswerClaim,
    AnswerEvidence,
    ClaimEvidenceLink,
    ClarificationRequest,
)
from rhetorica_backend.contracts.visualization import VisualizationSpec


class FinalTarget(BaseModel):
    """Optional target connector mapping in model output."""

    model_config = ConfigDict(extra="forbid")

    connector_name: str | None = None
    service: str | None = None
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")


class ChatFinalResponseContractV1(BaseModel):
    """Strict response payload expected from the model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["v1"] = "v1"
    answer_markdown: str = Field(
        min_length=1,
        validation_alias=AliasChoices("answer_markdown", "answer"),
    )
    sql: str | None
    target: FinalTarget | None
    claims: list[AnswerClaim]
    evidence: list[AnswerEvidence]
    claim_evidence_links: list[ClaimEvidenceLink]
    uncertainty_flags: list[str]
    clarification_request: ClarificationRequest | None = None
    analysis_document: dict[str, Any] | None = None
    visualization: VisualizationSpec | None = None

    @field_validator("answer_markdown")
    @classmethod
    def _validate_answer_markdown(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError
        return normalized

    def to_payload(self) -> dict[str, Any]:
        """Convert validated contract to orchestration payload format."""
        return {
            "answer": self.answer_markdown,
            "answer_markdown": self.answer_markdown,
            "sql": self.sql,
            "target": (self.target.model_dump(mode="json", by_alias=True) if self.target else None),
            "claims": [item.model_dump(mode="json") for item in self.claims],
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "claim_evidence_links": [
                item.model_dump(mode="json") for item in self.claim_evidence_links
            ],
            "uncertainty_flags": list(self.uncertainty_flags),
            "clarification_request": (
                self.clarification_request.model_dump(mode="json")
                if self.clarification_request is not None
                else None
            ),
            "analysis_document": self.analysis_document,
            "visualization": (
                self.visualization.model_dump(mode="json")
                if self.visualization is not None
                else None
            ),
        }


def final_response_json_schema() -> dict[str, Any]:
    """JSON schema for strict response_format providers."""
    return ChatFinalResponseContractV1.model_json_schema()


def build_final_response_payload(
    *,
    answer_markdown: str,
    sql: str | None = None,
    target: dict[str, Any] | None = None,
    claims: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    claim_evidence_links: list[dict[str, Any]] | None = None,
    uncertainty_flags: list[str] | None = None,
    clarification_request: dict[str, Any] | None = None,
    analysis_document: dict[str, Any] | None = None,
    visualization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a payload candidate matching ChatFinalResponseSchema v1."""
    return {
        "schema_version": "v1",
        "answer_markdown": answer_markdown,
        "sql": sql,
        "target": target,
        "claims": list(claims or []),
        "evidence": list(evidence or []),
        "claim_evidence_links": list(claim_evidence_links or []),
        "uncertainty_flags": list(uncertainty_flags or []),
        "clarification_request": clarification_request,
        "analysis_document": analysis_document,
        "visualization": visualization,
    }


def validate_final_response_payload(
    payload: dict[str, Any],
) -> tuple[ChatFinalResponseContractV1 | None, list[str]]:
    """Validate final payload and return compact validation errors."""
    try:
        validated = ChatFinalResponseContractV1.model_validate(payload)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
        ]
        return None, errors
    return validated, []
