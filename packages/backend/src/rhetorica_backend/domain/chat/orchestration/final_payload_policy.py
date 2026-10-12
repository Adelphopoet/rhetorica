"""Shared final payload policy for sync and streaming chat orchestration."""

from __future__ import annotations

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

THINKING_TAG_PATTERN = re.compile(r"<(think|thinking)>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
class FinalPayloadPolicy:
    """Single place for final payload acceptance and preservation rules."""

    @staticmethod
    def strip_thinking_tags(content: str) -> str:
        return THINKING_TAG_PATTERN.sub(" ", content)

    @staticmethod
    def parse_json_payload(raw_content: str) -> tuple[dict[str, Any], bool]:
        raw = raw_content.strip()
        if not raw.startswith("{") or not raw.endswith("}"):
            return {}, False
        try:
            import json

            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}, False
        if isinstance(parsed, dict):
            return parsed, True
        return {}, False

    @staticmethod
    def parse_strict_json_payload(raw_content: str) -> tuple[dict[str, Any], bool]:
        return FinalPayloadPolicy.parse_json_payload(raw_content)

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
    def extract_claim_evidence_links(llm_payload: dict[str, Any]) -> list[dict[str, Any]]:
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
    def _has_markdown_table(text: str) -> bool:
        return any(
            line.strip().startswith("|") and line.strip().endswith("|")
            for line in text.splitlines()
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
        answer = cls.to_optional_str(
            llm_payload.get("answer_markdown") or llm_payload.get("answer")
        )
        if answer is None:
            return False
        if not cls.has_supported_evidence(llm_payload, state):
            return False
        if cls.requires_sql_table_proof(request) and not cls.has_sql_backed_table_answer(
            llm_payload=llm_payload,
            state=state,
        ):
            state.add_warning("table_proof_required")
            return False
        return True

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
        return bool(cls.successful_execution_attempts(state))

    @classmethod
    def is_contract_final_payload(cls, raw_content: str) -> bool:
        parsed, parsed_ok = cls.parse_strict_json_payload(raw_content)
        if not parsed_ok:
            return False
        validated, _ = validate_final_response_payload(parsed)
        return validated is not None

    @classmethod
    def has_supported_evidence(
        cls,
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
                    state.metadata_sources or state.context_tables or state.metadata_table_columns
                ):
                    return False
                continue
            if evidence_type == "rag_insight":
                if "rag" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "internal_documentation":
                if "documentation" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "gitlab_file":
                if "gitlab" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "airflow_runtime":
                if "airflow" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "kubernetes_runtime":
                if "kubernetes" not in state.metadata_sources:
                    return False
                continue
            if evidence_type == "metrics_timeseries":
                if "victoriametrics" not in state.metadata_sources:
                    return False
                continue
            return False
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
        """Semantic routing belongs to the agent, not backend text classification."""
        _ = request
        return False

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
