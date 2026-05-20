"""Deterministic extraction of final JSON candidates from provider wrappers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class FinalPayloadJsonCandidate:
    """Candidate JSON text selected for strict contract validation."""

    text: str
    source: str


class FinalPayloadJsonExtractor:
    """Extracts one JSON object candidate without deciding if it is valid."""

    def extract(self, raw_content: str) -> FinalPayloadJsonCandidate:
        raw = raw_content.strip()
        if not raw:
            return FinalPayloadJsonCandidate(text=raw, source="empty")
        if self._is_json_object(raw):
            return FinalPayloadJsonCandidate(text=raw, source="raw")

        fenced = self._extract_single_fenced_json(raw)
        if fenced is not None:
            return FinalPayloadJsonCandidate(text=fenced, source="fenced_json")

        balanced = self._extract_single_balanced_object(raw)
        if balanced is not None:
            return FinalPayloadJsonCandidate(text=balanced, source="balanced_json")

        return FinalPayloadJsonCandidate(text=raw, source="raw_unparsed")

    @staticmethod
    def _is_json_object(value: str) -> bool:
        if not value.startswith("{") or not value.endswith("}"):
            return False
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict)

    @staticmethod
    def _extract_single_fenced_json(value: str) -> str | None:
        matches: list[str] = []
        for match in _FENCED_JSON_RE.finditer(value):
            candidate = match.group(1).strip()
            if FinalPayloadJsonExtractor._is_json_object(candidate):
                matches.append(candidate)
        if len(matches) != 1:
            return None
        return matches[0]

    @classmethod
    def _extract_single_balanced_object(cls, value: str) -> str | None:
        candidates: list[str] = []
        depth = 0
        start: int | None = None
        in_string = False
        escaped = False

        for index, char in enumerate(value):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
                continue
            if char != "}":
                continue

            depth -= 1
            if depth != 0 or start is None:
                continue
            candidate = value[start : index + 1].strip()
            if cls._is_json_object(candidate):
                candidates.append(candidate)
            start = None

        if len(candidates) != 1:
            return None
        return candidates[0]
