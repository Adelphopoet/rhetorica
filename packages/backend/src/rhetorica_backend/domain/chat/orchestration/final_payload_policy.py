"""Shared final payload policy for sync and streaming chat orchestration."""

from __future__ import annotations

import json
import re
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    validate_final_response_payload,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    is_explicit_sql_inspection_request,
)

THINKING_TAG_PATTERN = re.compile(
    r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL
)
MARKDOWN_STRUCTURE_PATTERN = re.compile(
    r"(^|\n)(?:#{1,6}\s|\s*[-*+]\s|\s*\d+\.\s|\|.+\|\s*$|```|>\s)",
    flags=re.MULTILINE,
)


class FinalPayloadPolicy:
    """Single place for final payload acceptance and preservation rules."""

    @staticmethod
    def strip_thinking_tags(content: str) -> str:
        return THINKING_TAG_PATTERN.sub(" ", content)

    @staticmethod
    def parse_json_payload(raw_content: str) -> tuple[dict[str, Any], bool]:
        raw = raw_content.strip()
        if raw.startswith("```"):
            lines = [
                line
                for line in raw.splitlines()
                if not line.strip().startswith("```")
            ]
            raw = "\n".join(lines).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            left = raw.find("{")
            right = raw.rfind("}")
            if left == -1 or right == -1 or right <= left:
                return {}, False
            try:
                parsed = json.loads(raw[left : right + 1])
            except json.JSONDecodeError:
                return {}, False
            else:
                if isinstance(parsed, dict):
                    return parsed, True
                return {}, False
        else:
            if isinstance(parsed, dict):
                return parsed, True
            return {}, False

    @staticmethod
    def extract_claims(llm_payload: dict[str, Any]) -> list[dict[str, Any]]:
        claims = llm_payload.get("claims")
        if not isinstance(claims, list):
            return []
        return [item for item in claims if isinstance(item, dict)]

    @staticmethod
    def extract_evidence(llm_payload: dict[str, Any]) -> list[dict[str, Any]]:
        evidence = llm_payload.get("evidence")
        if not isinstance(evidence, list):
            return []
        return [item for item in evidence if isinstance(item, dict)]

    @staticmethod
    def extract_claim_evidence_links(
        llm_payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        links = llm_payload.get("claim_evidence_links")
        if not isinstance(links, list):
            return []
        return [item for item in links if isinstance(item, dict)]

    @staticmethod
    def extract_uncertainty_flags(llm_payload: dict[str, Any]) -> list[str]:
        flags = llm_payload.get("uncertainty_flags")
        if not isinstance(flags, list):
            return []
        return [str(item) for item in flags if isinstance(item, str)]

    @staticmethod
    def to_optional_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def has_structured_markdown(text: str | None) -> bool:
        if not isinstance(text, str):
            return False
        return bool("\n\n" in text or MARKDOWN_STRUCTURE_PATTERN.search(text))

    @staticmethod
    def looks_like_progress_text(text: str | None) -> bool:
        if not isinstance(text, str):
            return False
        normalized = text.casefold()
        return any(
            marker in normalized
            for marker in (
                "сначала ",
                "проверю",
                "ищу",
                "поищу",
                "searching",
                "looking",
                "checking",
                "i will ",
            )
        )

    @classmethod
    def looks_like_nonfinal_raw_payload(cls, text: str | None) -> bool:
        if not isinstance(text, str) or not text.strip():
            return False
        parsed, parsed_ok = cls.parse_json_payload(text)
        if not parsed_ok:
            return False
        if parsed.get("schema_version") == "v1":
            return True
        if parsed.get("next_action") in {
            "tool_call",
            "final_answer",
            "needs_user_clarification",
        }:
            return True
        contractish_keys = {
            "answer",
            "answer_markdown",
            "sql",
            "target",
            "tool_request",
            "claims",
            "evidence",
            "claim_evidence_links",
            "uncertainty_flags",
        }
        if not contractish_keys.intersection(parsed):
            return False
        answer = cls.to_optional_str(parsed.get("answer_markdown") or parsed.get("answer"))
        if answer and cls.looks_like_progress_text(answer):
            return True
        return "tool_request" in parsed or "target" in parsed

    @classmethod
    def preserve_raw_markdown_answer(
        cls,
        *,
        raw_final_text: str,
        parsed_payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_answer = cls.to_optional_str(cls.strip_thinking_tags(raw_final_text))
        candidate_answer = cls.to_optional_str(parsed_payload.get("answer"))
        if raw_answer is None or candidate_answer is None or raw_answer == candidate_answer:
            return parsed_payload
        if cls.looks_like_nonfinal_raw_payload(raw_answer):
            return parsed_payload
        if (
            cls._has_explicit_visual_output(parsed_payload)
            and "```" in raw_answer
            and not cls._has_markdown_table(raw_answer)
        ):
            return parsed_payload
        if not cls._should_preserve_raw_markdown_answer(
            raw_answer=raw_answer,
            candidate_answer=candidate_answer,
        ):
            return parsed_payload
        preserved_payload = dict(parsed_payload)
        preserved_payload["answer"] = raw_answer
        preserved_payload["answer_markdown"] = raw_answer
        return preserved_payload

    @classmethod
    def preserve_raw_markdown_answer_from_payload(
        cls,
        *,
        raw_final_text: str,
        parsed_raw_payload: dict[str, Any] | None,
        parsed_payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_payload_answer = None
        if isinstance(parsed_raw_payload, dict):
            raw_payload_answer = cls.to_optional_str(
                parsed_raw_payload.get("answer_markdown")
                or parsed_raw_payload.get("answer")
            )
        return cls.preserve_raw_markdown_answer(
            raw_final_text=raw_payload_answer or raw_final_text,
            parsed_payload=parsed_payload,
        )

    @staticmethod
    def _should_preserve_raw_markdown_answer(
        *,
        raw_answer: str,
        candidate_answer: str,
    ) -> bool:
        if not FinalPayloadPolicy.has_structured_markdown(raw_answer):
            return False
        if not FinalPayloadPolicy.has_structured_markdown(candidate_answer):
            return True
        if FinalPayloadPolicy._has_markdown_table(raw_answer) and not (
            FinalPayloadPolicy._has_markdown_table(candidate_answer)
        ):
            return True
        return len(candidate_answer.strip()) < int(len(raw_answer.strip()) * 0.6)

    @staticmethod
    def _has_markdown_table(text: str) -> bool:
        return any(
            line.strip().startswith("|") and line.strip().endswith("|")
            for line in text.splitlines()
        )

    @staticmethod
    def _has_explicit_visual_output(parsed_payload: dict[str, Any]) -> bool:
        if isinstance(parsed_payload.get("visualization"), dict):
            return True
        analysis_document = parsed_payload.get("analysis_document")
        if not isinstance(analysis_document, dict):
            return False
        blocks = analysis_document.get("blocks")
        if not isinstance(blocks, list):
            return False
        return any(
            isinstance(block, dict) and block.get("kind") == "chart"
            for block in blocks
        )

    @staticmethod
    def has_needs_user_clarification_flag(llm_payload: dict[str, Any]) -> bool:
        flags = llm_payload.get("uncertainty_flags")
        if not isinstance(flags, list):
            return False
        normalized_flags = {
            str(flag).strip().lower()
            for flag in flags
            if isinstance(flag, str) and str(flag).strip()
        }
        return "needs_user_clarification" in normalized_flags

    @classmethod
    def is_acceptable_final_payload(
        cls,
        *,
        llm_payload: dict[str, Any],
        state: ToolOrchestrationState,
        request: ChatAskRequest,
    ) -> bool:
        if cls.has_needs_user_clarification_flag(llm_payload):
            return True
        if not cls.has_supported_evidence(llm_payload, state):
            return False
        if cls.requires_sql_table_proof(request) and not cls.has_sql_backed_table_answer(
            llm_payload=llm_payload,
            state=state,
        ):
            state.add_warning("table_proof_required")
            return False
        if cls.requires_metadata_lookup(request) and not cls.has_non_request_evidence(
            llm_payload
        ):
            return False
        if cls.successful_execution_attempts(state):
            return cls.has_runtime_execution_backed_answer(
                llm_payload=llm_payload,
                state=state,
            )
        if cls.has_successful_jira_create_backed_answer(
            llm_payload=llm_payload,
            state=state,
        ):
            return True
        if state.tool_call_count == 0:
            return True
        claims = llm_payload.get("claims")
        if isinstance(claims, list) and claims:
            return True
        evidence = llm_payload.get("evidence")
        return isinstance(evidence, list) and bool(evidence)

    @classmethod
    def has_successful_jira_create_backed_answer(
        cls,
        *,
        llm_payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> bool:
        answer = cls.to_optional_str(
            llm_payload.get("answer_markdown") or llm_payload.get("answer")
        )
        if answer is None:
            return False
        normalized_answer = answer.casefold()
        for (tool_name, _), result in state.tool_call_results.items():
            if tool_name != "create_jira_issue":
                continue
            if not isinstance(result, dict) or result.get("ok") is not True:
                continue
            issue_key = cls.to_optional_str(result.get("issue_key"))
            if issue_key is not None and issue_key.casefold() in normalized_answer:
                return True
        return False

    @classmethod
    def has_runtime_execution_backed_answer(
        cls,
        *,
        llm_payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> bool:
        claims = cls.extract_claims(llm_payload)
        evidence = cls.extract_evidence(llm_payload)
        links = cls.extract_claim_evidence_links(llm_payload)
        if not claims or not evidence or not links:
            return False

        execution_evidence_ids: set[str] = set()
        for item in evidence:
            evidence_type = str(item.get("type") or "").strip().lower()
            if evidence_type != "execution_result":
                continue
            reference = str(item.get("reference") or "").strip()
            if cls.execution_attempt_by_reference(state=state, reference=reference) is None:
                continue
            evidence_id = cls.to_optional_str(item.get("evidence_id"))
            if evidence_id is not None:
                execution_evidence_ids.add(evidence_id)

        if not execution_evidence_ids:
            return False

        for link in links:
            evidence_ids = link.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                continue
            if any(
                isinstance(evidence_id, str) and evidence_id in execution_evidence_ids
                for evidence_id in evidence_ids
            ):
                return True
        return False

    @classmethod
    def requires_sql_table_proof(cls, request: ChatAskRequest) -> bool:
        normalized = request.question.casefold()
        return any(
            marker in normalized
            for marker in (
                "докажи на таблицах",
                "покажи на таблицах",
                "prove with tables",
                "table proof",
            )
        )

    @classmethod
    def has_sql_backed_table_answer(
        cls,
        *,
        llm_payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> bool:
        answer = cls.to_optional_str(
            llm_payload.get("answer_markdown") or llm_payload.get("answer")
        )
        if answer is None or "|" not in answer:
            return False
        if not cls.successful_execution_attempts(state):
            return False
        return cls.has_runtime_execution_backed_answer(
            llm_payload=llm_payload,
            state=state,
        )

    @classmethod
    def is_contract_final_payload(cls, raw_content: str) -> bool:
        parsed, parsed_ok = cls.parse_json_payload(raw_content)
        if not parsed_ok:
            return False
        validated, _ = validate_final_response_payload(parsed)
        return validated is not None

    @staticmethod
    def has_supported_evidence(
        llm_payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> bool:
        evidence = llm_payload.get("evidence")
        if not isinstance(evidence, list):
            return True
        for item in evidence:
            if not isinstance(item, dict):
                return False
            evidence_type = str(item.get("type") or "").strip().lower()
            reference = str(item.get("reference") or "").strip()
            if evidence_type == "execution_result":
                if state.execution is None:
                    return False
                continue
            if evidence_type == "metadata_entity":
                if reference.startswith("request:"):
                    continue
                if not (
                    state.metadata_sources
                    or state.context_tables
                    or state.metadata_table_columns
                ):
                    return False
                continue
            if evidence_type == "rag_insight":
                if "rag" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "confluence_page":
                if "confluence" not in state.metadata_sources:
                    return False
                continue
        return True

    @staticmethod
    def has_non_request_evidence(llm_payload: dict[str, Any]) -> bool:
        evidence = llm_payload.get("evidence")
        if not isinstance(evidence, list):
            return False
        for item in evidence:
            if not isinstance(item, dict):
                continue
            reference = str(item.get("reference") or "").strip()
            if reference and not reference.startswith("request:"):
                return True
        return False

    @staticmethod
    def requires_metadata_lookup(request: ChatAskRequest) -> bool:
        normalized = request.question.casefold()
        if is_explicit_sql_inspection_request(request.question):
            return False
        asks_definition = any(
            phrase in normalized
            for phrase in (
                "что такое",
                "what is",
                "what's",
                "опиши",
                "describe",
                "что за",
            )
        )
        asks_metadata_discovery = any(
            phrase in normalized
            for phrase in (
                "витрин",
                "таблиц",
                "table",
                "metadata",
                "метадан",
                "каталог",
                "entity",
            )
        ) and any(
            phrase in normalized
            for phrase in (
                "есть",
                "find",
                "найд",
                "ищ",
                "search",
                "look up",
            )
        )
        if not asks_definition and not asks_metadata_discovery:
            return False
        return bool(
            re.search(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", normalized)
            or re.search(r"\b(?:ft|dm|dds|ods|stg|mart)_[a-z0-9_]+\b", normalized)
        )

    @classmethod
    def successful_execution_attempts(
        cls,
        state: ToolOrchestrationState,
    ) -> list[SqlExecutionAttempt]:
        return [
            attempt
            for attempt in state.execution_attempts
            if attempt.execution.status == "succeeded"
            and not cls.is_introspection_execution_sql(attempt.sql)
        ]

    @staticmethod
    def execution_attempt_by_reference(
        *,
        state: ToolOrchestrationState,
        reference: str,
    ) -> SqlExecutionAttempt | None:
        normalized = reference.strip()
        if not normalized:
            return None
        if normalized == "execution":
            attempts = FinalPayloadPolicy.successful_execution_attempts(state)
            if len(attempts) == 1:
                return attempts[0]
            if state.execution is not None:
                for attempt in reversed(attempts):
                    if attempt.execution.execution_id == state.execution.execution_id:
                        return attempt
            return None
        for attempt in reversed(state.execution_attempts):
            execution_id = attempt.execution.execution_id
            if execution_id and execution_id == normalized:
                return attempt
        return None

    @staticmethod
    def is_introspection_execution_sql(sql: str | None) -> bool:
        if not isinstance(sql, str) or not sql.strip():
            return False
        normalized = re.sub(r"\s+", " ", sql.strip().lower())
        introspection_prefixes = (
            "show tables",
            "show columns",
            "show create",
            "show schemas",
            "show catalogs",
            "describe ",
            "desc ",
            "explain ",
        )
        if normalized.startswith(introspection_prefixes):
            return True
        introspection_markers = (
            " from information_schema.tables",
            " from information_schema.columns",
            " from information_schema.schemata",
            " from system.tables",
            " from system.columns",
        )
        return any(marker in normalized for marker in introspection_markers)
