"""Explainability payload enrichment for chat final responses."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    ToolOrchestrationState,
)

SQL_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:sql)?\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL
)


class ExplainabilityPayloadEnricher:
    """Builds evidence/claims scaffolding when the LLM response omitted it."""

    @classmethod
    def augment_payload(
        cls,
        *,
        payload: dict[str, Any],
        state: ToolOrchestrationState,
        resolve_execution_from_payload: Callable[
            [dict[str, Any], ToolOrchestrationState], ExecuteSqlResponse | None
        ],
    ) -> dict[str, Any]:
        claims = FinalPayloadPolicy.extract_claims(payload)
        evidence = FinalPayloadPolicy.extract_evidence(payload)
        links = FinalPayloadPolicy.extract_claim_evidence_links(payload)
        if claims and evidence and links:
            return payload

        primary_execution = resolve_execution_from_payload(payload, state)
        synthesized_evidence = cls.synthesize_evidence(
            state=state,
            primary_execution=primary_execution,
        )
        if not synthesized_evidence:
            return payload

        answer = FinalPayloadPolicy.to_optional_str(payload.get("answer")) or "Ответ сформирован."
        augmented = dict(payload)
        augmented["claims"] = claims or cls.synthesize_claims(answer=answer)
        augmented["evidence"] = evidence or synthesized_evidence
        augmented["claim_evidence_links"] = links or cls.synthesize_claim_links(
            claims=augmented["claims"],
            evidence=augmented["evidence"],
        )
        state.add_warning("explainability_synthesized")
        augmented["warnings"] = state.warnings
        return augmented

    @classmethod
    def synthesize_evidence(
        cls,
        *,
        state: ToolOrchestrationState,
        primary_execution: ExecuteSqlResponse | None = None,
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        selected_execution = primary_execution or state.execution
        if selected_execution is not None:
            evidence_id = "e_exec_1"
            seen.add(evidence_id)
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "type": "execution_result",
                    "reference": selected_execution.execution_id or "execution",
                    "title": "SQL execution result",
                    "snippet": cls.execution_snippet(selected_execution),
                    "status": "verified",
                }
            )

        for result in state.tool_call_results.values():
            if not isinstance(result, dict) or result.get("ok") is not True:
                continue
            raw_items = result.get("results")
            if not isinstance(raw_items, list):
                raw_items = result.get("tables")
            if not isinstance(raw_items, list):
                raw_entity = result.get("entity")
                raw_items = [raw_entity] if isinstance(raw_entity, dict) else []
            for item in raw_items[:3]:
                if not isinstance(item, dict):
                    continue
                candidate = cls.evidence_from_tool_result_item(item=item, state=state)
                if candidate is None:
                    continue
                evidence_id = str(candidate.get("evidence_id") or "")
                if not evidence_id or evidence_id in seen:
                    continue
                seen.add(evidence_id)
                evidence.append(candidate)
                if len(evidence) >= 4:
                    return evidence

        if not evidence and state.context_tables:
            for index, table in enumerate(state.context_tables[:3], start=1):
                snippet = cls.semantic_candidate_snippet_from_state(
                    state=state,
                    reference=table,
                ) or f"Metadata context table: {table}"
                evidence.append(
                    {
                        "evidence_id": f"e_meta_{index}",
                        "type": "metadata_entity",
                        "reference": table,
                        "title": table.split(".")[-1],
                        "snippet": snippet,
                        "status": "verified",
                    }
                )
        return evidence

    @classmethod
    def evidence_from_tool_result_item(
        cls,
        *,
        item: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any] | None:
        if "confluence" in state.metadata_sources and (
            isinstance(item.get("url"), str) or isinstance(item.get("excerpt"), str)
        ):
            reference = str(item.get("url") or item.get("id") or item.get("title") or "")
            if not reference:
                return None
            return {
                "evidence_id": f"e_conf_{abs(hash(reference)) % 100000}",
                "type": "confluence_page",
                "reference": reference,
                "title": FinalPayloadPolicy.to_optional_str(item.get("title")),
                "snippet": FinalPayloadPolicy.to_optional_str(item.get("excerpt")),
                "status": "verified",
            }
        if "rag" in state.metadata_sources and (
            isinstance(item.get("citation"), str) or isinstance(item.get("summary"), str)
        ):
            reference = str(item.get("citation") or item.get("id") or "")
            if not reference:
                return None
            return {
                "evidence_id": f"e_rag_{abs(hash(reference)) % 100000}",
                "type": "rag_insight",
                "reference": reference,
                "title": FinalPayloadPolicy.to_optional_str(item.get("id")),
                "snippet": FinalPayloadPolicy.to_optional_str(item.get("summary")),
                "status": "verified",
            }
        fqn = item.get("fullyQualifiedName") or item.get("fqn")
        if isinstance(fqn, str) and fqn.strip():
            snippet = cls.semantic_item_snippet(item) or FinalPayloadPolicy.to_optional_str(
                item.get("description")
            )
            return {
                "evidence_id": f"e_meta_{abs(hash(fqn)) % 100000}",
                "type": "metadata_entity",
                "reference": fqn.strip(),
                "title": FinalPayloadPolicy.to_optional_str(item.get("displayName"))
                or fqn.strip().split(".")[-1],
                "snippet": snippet,
                "status": "verified",
            }
        return None

    @classmethod
    def semantic_candidate_snippet_from_state(
        cls,
        *,
        state: ToolOrchestrationState,
        reference: str,
    ) -> str | None:
        if state.semantic_grounding is None:
            return None
        for candidate in state.semantic_grounding.candidates:
            if candidate.fqn == reference:
                return cls.semantic_item_snippet(candidate.model_dump(mode="json"))
        return None

    @classmethod
    def semantic_item_snippet(cls, item: dict[str, Any]) -> str | None:
        parts: list[str] = []
        certification = FinalPayloadPolicy.to_optional_str(item.get("certification"))
        if certification:
            parts.append(f"certification={certification}")
        tier = FinalPayloadPolicy.to_optional_str(item.get("tier"))
        if tier:
            parts.append(f"tier={tier}")
        owners = item.get("owners")
        if isinstance(owners, list):
            owner_values = [
                str(owner).strip()
                for owner in owners
                if isinstance(owner, str) and owner.strip()
            ][:3]
            if owner_values:
                parts.append("owners=" + ", ".join(owner_values))
        domains = item.get("domains")
        if isinstance(domains, list):
            domain_values = [
                str(domain).strip()
                for domain in domains
                if isinstance(domain, str) and domain.strip()
            ][:3]
            if domain_values:
                parts.append("domains=" + ", ".join(domain_values))
        matched_on = item.get("matched_on")
        if isinstance(matched_on, list):
            matched_values = [
                str(value).strip()
                for value in matched_on
                if isinstance(value, str) and value.strip()
            ][:5]
            if matched_values:
                parts.append("matched_on=" + ", ".join(matched_values))
        suspicious = item.get("suspicious")
        if isinstance(suspicious, bool):
            parts.append(f"suspicious={'yes' if suspicious else 'no'}")
        if parts:
            return "Metadata signals: " + "; ".join(parts)
        return FinalPayloadPolicy.to_optional_str(item.get("description"))

    @staticmethod
    def execution_snippet(execution: ExecuteSqlResponse | None) -> str:
        if execution is None:
            return ""
        preview = execution.preview
        row_count = (
            execution.row_count
            if execution.row_count is not None
            else len(preview.rows)
        )
        if preview.columns:
            return f"columns={', '.join(preview.columns[:6])}; rows={row_count}"
        return f"rows={row_count}"

    @staticmethod
    def synthesize_claims(answer: str) -> list[dict[str, Any]]:
        cleaned = SQL_CODE_BLOCK_PATTERN.sub(" ", answer)
        fragments = [
            fragment.strip(" -\n\t")
            for fragment in re.split(r"[.!?\n]+", cleaned)
            if fragment.strip(" -\n\t")
        ]
        if not fragments:
            return []
        return [
            {
                "claim_id": "c1",
                "text": fragments[0][:280],
                "status": "verified",
            }
        ]

    @staticmethod
    def synthesize_claim_links(
        *,
        claims: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        evidence_ids = [
            str(item.get("evidence_id"))
            for item in evidence
            if isinstance(item.get("evidence_id"), str)
        ]
        if not claims or not evidence_ids:
            return []
        return [
            {
                "claim_id": claim["claim_id"],
                "evidence_ids": evidence_ids,
            }
            for claim in claims
            if isinstance(claim.get("claim_id"), str)
        ]
