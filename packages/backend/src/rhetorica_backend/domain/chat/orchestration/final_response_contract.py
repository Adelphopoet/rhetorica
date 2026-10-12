"""Strict final response contract for LLM chat runtime."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from rhetorica_backend.contracts.chat import (
    ClarificationRequest,
)
from rhetorica_backend.contracts.visualization import VisualizationSpec

SUBMIT_FINAL_ANSWER_TOOL_NAME = "submit_final_answer"
_JSON_CODE_FENCE_PATTERN = re.compile(
    r"```(?:json)?[^\n]*\n(?P<body>[\s\S]*?)```",
    flags=re.IGNORECASE,
)


class ChatFinalResponseContractV1(BaseModel):
    """Minimal final-answer payload expected from the model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["v1"] = "v1"
    answer_markdown: str = Field(
        min_length=1,
        description=(
            "User-visible Markdown only. Do not include visualization JSON, tool "
            "payloads, or protocol wrappers here."
        ),
    )
    uncertainty_flags: list[str] = Field(default_factory=list)
    clarification_request: ClarificationRequest | None = None
    visualization: VisualizationSpec | None = None

    @field_validator("answer_markdown")
    @classmethod
    def _validate_answer_markdown(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError
        if _contains_visualization_protocol_payload(normalized):
            raise ValueError(
                "answer_markdown must not contain visualization JSON; put charts in "
                "the visualization field only"
            )
        return normalized

    def to_payload(self) -> dict[str, Any]:
        """Convert validated contract to orchestration payload format."""
        return {
            "answer": self.answer_markdown,
            "answer_markdown": self.answer_markdown,
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": list(self.uncertainty_flags),
            "clarification_request": (
                self.clarification_request.model_dump(mode="json")
                if self.clarification_request is not None
                else None
            ),
            "analysis_document": None,
            "visualization": (
                self.visualization.model_dump(mode="json")
                if self.visualization is not None
                else None
            ),
        }


def final_response_json_schema() -> dict[str, Any]:
    """JSON schema for strict response_format providers."""
    schema = ChatFinalResponseContractV1.model_json_schema()
    clarification_choice = schema.get("$defs", {}).get("ClarificationChoice")
    if isinstance(clarification_choice, dict):
        properties = clarification_choice.get("properties")
        if isinstance(properties, dict):
            # This extensibility bag is backend-owned and cannot be represented by
            # OpenAI strict schemas, which forbid open object properties.
            properties.pop("metadata", None)
    return schema


def final_response_tool_schema() -> dict[str, Any]:
    """Native Chat Completions tool used to submit the final answer contract."""
    return {
        "type": "function",
        "function": {
            "name": SUBMIT_FINAL_ANSWER_TOOL_NAME,
            "description": (
                "End the current request and submit the final user-facing answer. Call "
                "this only when the answer is ready: never use it for a progress update, "
                "a promise to use another tool, or a description of the next step. Respect "
                "the user's explicit completion criteria. A supported partial answer is "
                "valid only when its limitations are stated and the user's request allows it. "
                "For a simple reply, put the complete answer in answer_markdown, set "
                "schema_version to v1, uncertainty_flags to [], and both "
                "clarification_request and visualization to null."
            ),
            "parameters": final_response_json_schema(),
            "strict": True,
        },
    }


def validate_final_response_payload(
    payload: dict,
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


def _contains_visualization_protocol_payload(markdown: str) -> bool:
    """Reject chart specs embedded in user-visible Markdown."""
    if _is_visualization_protocol_json(markdown):
        return True
    for match in _JSON_CODE_FENCE_PATTERN.finditer(markdown):
        if _is_visualization_protocol_json(match.group("body")):
            return True
    return False


def _is_visualization_protocol_json(value: str) -> bool:
    try:
        payload = json.loads(value.strip())
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    visualization = payload.get("visualization")
    if isinstance(visualization, dict):
        return True
    return (
        payload.get("version") in {"v1", "v2"}
        and isinstance(payload.get("kind"), str)
        and isinstance(payload.get("data_source"), dict)
        and isinstance(payload.get("encoding"), dict)
    )
