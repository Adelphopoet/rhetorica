"""Semantic grounding payload construction for metadata candidates."""

from __future__ import annotations

from typing import Any, ClassVar

from rhetorica_backend.domain.chat.metadata.models import TableContext


class MetadataSemanticGroundingService:
    """Translate ranked metadata matches into stable semantic grounding payloads."""

    SUSPICIOUS_SCOPE_TOKENS: ClassVar[set[str]] = {
        "adhoc",
        "archive",
        "backup",
        "demo",
        "dev",
        "example",
        "examples",
        "mock",
        "playground",
        "poc",
        "qa",
        "sandbox",
        "scratch",
        "staging",
        "temp",
        "test",
        "tmp",
    }

    @classmethod
    def is_suspicious_table(cls, table: TableContext) -> bool:
        scope_values = (
            table.fqn,
            table.service,
            table.database,
            table.schema,
        )
        return any(
            any(
                isinstance(value, str) and token in value.lower()
                for value in scope_values
            )
            for token in cls.SUSPICIOUS_SCOPE_TOKENS
        )

    @classmethod
    def semantic_reason_codes(cls, table: TableContext) -> list[str]:
        reason_codes: list[str] = []
        matched_on = set(table.matched_on)
        if any(
            [
                table.certification,
                table.tier,
                table.owners,
                table.owner_teams,
                table.domains,
                table.glossary_terms,
                table.related_metrics,
                table.description,
                table.usage_count > 0,
            ]
        ):
            reason_codes.append("governance_strength")
        if "glossary_term_partial" in matched_on or table.glossary_terms:
            reason_codes.append("glossary_term_match")
        if "metric_partial" in matched_on or table.related_metrics:
            reason_codes.append("metric_context_match")
        if "quality_failed" in matched_on:
            reason_codes.append("quality_failed")
        if "quality_aborted" in matched_on:
            reason_codes.append("quality_aborted")
        if "business_schema" in matched_on:
            reason_codes.append("business_layer_preference")
        if "specialized_variant_penalty" in matched_on:
            reason_codes.append("specialized_variant_penalty")
        if "suspicious_scope_penalty" in matched_on or cls.is_suspicious_table(table):
            reason_codes.append("suspicious_scope_penalty")
        if any(item.startswith("preferred_") for item in matched_on):
            reason_codes.append("preferred_connector_map_match")
        return reason_codes

    @classmethod
    def semantic_candidate_payload(cls, table: TableContext) -> dict[str, Any]:
        return {
            "fqn": table.fqn,
            "service": table.service,
            "database": table.database,
            "schema": table.schema,
            "relevance_score": round(table.relevance_score, 3),
            "matched_on": list(table.matched_on),
            "reason_codes": cls.semantic_reason_codes(table),
            "certification": table.certification,
            "tier": table.tier,
            "owners": list(table.owners[:10]),
            "owner_teams": list(table.owner_teams[:10]),
            "domains": list(table.domains[:10]),
            "glossary_terms": [ref.name for ref in table.glossary_terms[:10]],
            "related_metrics": [ref.name for ref in table.related_metrics[:10]],
            "quality_status": (
                table.quality_summary.status
                if table.quality_summary is not None
                else None
            ),
            "quality_summary": (
                table.quality_summary.to_payload()
                if table.quality_summary is not None
                else None
            ),
            "usage_count": int(table.usage_count),
            "suspicious": cls.is_suspicious_table(table),
        }

    @classmethod
    def build_semantic_grounding_payload(
        cls,
        tables: list[TableContext],
        *,
        chosen_table_fqn: str | None = None,
        lineage_summary: str | None = None,
    ) -> dict[str, Any] | None:
        if not tables and not chosen_table_fqn and not lineage_summary:
            return None

        candidates = [
            cls.semantic_candidate_payload(table)
            for table in tables[:3]
            if isinstance(table.fqn, str) and table.fqn.strip()
        ]
        chosen = None
        if chosen_table_fqn:
            chosen = chosen_table_fqn
        elif len(candidates) == 1:
            # Single candidate — no ambiguity possible
            chosen = str(candidates[0]["fqn"])
        # else: multiple ambiguous candidates — do not auto-pick; caller must clarify

        # Track whether the choice was explicit or derived
        explicit_choice = chosen_table_fqn is not None
        return {
            "chosen_table_fqn": chosen,
            "candidates": candidates,
            "selection_rationale": cls._selection_rationale(
                tables=tables[:3],
                chosen_table_fqn=chosen,
            ) if chosen is not None else None,
            "explicit_choice": explicit_choice,
            "lineage_summary": lineage_summary,
        }

    @classmethod
    def _selection_rationale(
        cls,
        *,
        tables: list[TableContext],
        chosen_table_fqn: str | None,
    ) -> str | None:
        chosen = next(
            (
                table
                for table in tables
                if chosen_table_fqn
                and isinstance(table.fqn, str)
                and table.fqn == chosen_table_fqn
            ),
            tables[0] if tables else None,
        )
        if chosen is None:
            return None

        reason_texts: list[str] = []
        reason_codes = cls.semantic_reason_codes(chosen)
        if "governance_strength" in reason_codes:
            reason_texts.append("strong governance signals")
        if "business_layer_preference" in reason_codes:
            reason_texts.append("business-layer schema")
        if "preferred_connector_map_match" in reason_codes:
            reason_texts.append("connector-map match")

        alternatives = [table for table in tables if table.fqn != chosen.fqn]
        suspicious = cls.is_suspicious_table(chosen)
        weak_match = chosen.relevance_score <= 0 or (suspicious and not reason_texts)
        close_alternative = bool(
            alternatives and (chosen.relevance_score - alternatives[0].relevance_score) < 12.0
        )

        table_name = chosen.fqn
        if weak_match:
            note = "best available match is weak"
            if suspicious:
                note += " and sits in a suspicious scope"
            if alternatives:
                note += "; alternatives remain plausible"
            return f"Selected `{table_name}` because {note}."

        if not reason_texts:
            reason_texts.append("it has the strongest metadata match")
        rationale = ", ".join(reason_texts[:3])
        sentence = f"Selected `{table_name}` because it has {rationale}."
        if suspicious:
            sentence += " Scope still looks suspicious."
        elif close_alternative:
            sentence += " Alternatives were close, so treat the choice as governed but not absolute."
        return sentence
