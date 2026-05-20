"""Ranking and filtering for metadata search candidates."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.models import TableContext
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.semantic_grounding_service import (
    MetadataSemanticGroundingService,
)
from rhetorica_backend.domain.chat.metadata.usage_store import (
    InMemoryMetadataUsageStore,
    MetadataUsageStore,
)


@dataclass(frozen=True)
class MetadataTableCandidate:
    """Scoped table candidate passed from generation into reranking."""

    table: TableContext
    usage_frequency: float


class MetadataTableCandidateGenerator:
    """Generate scope-valid table candidates before reranking."""

    def generate(
        self,
        *,
        tables: list[TableContext],
        service: str | None,
        database: str | None,
        schema: str | None,
        usage_map: dict[str, float],
        logger: logging.Logger,
    ) -> tuple[list[MetadataTableCandidate], int]:
        candidates: list[MetadataTableCandidate] = []
        scope_filtered_count = 0
        for table in tables:
            mismatch_reason = self._scope_mismatch_reason(
                table=table,
                service=service,
                database=database,
                schema=schema,
            )
            if mismatch_reason is not None:
                scope_filtered_count += 1
                logger.debug(
                    "metadata.table_ranking.candidate_skipped",
                    extra={
                        "table_fqn": table.fqn,
                        "reason": mismatch_reason,
                        "table_service": table.service,
                        "table_database": table.database,
                        "table_schema": table.schema,
                        "service_filter": service,
                        "database_filter": database,
                        "schema_filter": schema,
                    },
                )
                continue
            candidates.append(
                MetadataTableCandidate(
                    table=table,
                    usage_frequency=usage_map.get(table.fqn, 0.0),
                )
            )
        return candidates, scope_filtered_count

    @classmethod
    def _scope_mismatch_reason(
        cls,
        *,
        table: TableContext,
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> str | None:
        if not cls._matches(table.service, service):
            return "service_filter_mismatch"
        if not cls._matches(table.database, database):
            return "database_filter_mismatch"
        if not cls._matches(table.schema, schema):
            return "schema_filter_mismatch"
        return None

    @staticmethod
    def _matches(value: str | None, expected: str | None) -> bool:
        if expected is None:
            return True
        if value is None:
            return False
        return value.strip().lower() == expected.strip().lower()


class MetadataRankingService:
    """Score, filter, and prioritize metadata candidates."""

    _IDENTIFIER_MATCH_ORDER: ClassVar[dict[str, int]] = {
        "none": 0,
        "fqn_contains": 0,
        "partial_leaf": 0,
        "prefix_leaf": 0,
        "exact_leaf": 4,
    }
    _TIER_WEIGHTS: ClassVar[dict[str, float]] = {
        "tier.tier1": 18.0,
        "tier.tier2": 12.0,
        "tier.tier3": 7.0,
    }
    _CERTIFICATION_WEIGHTS: ClassVar[dict[str, float]] = {
        "certification.gold": 28.0,
        "certification.silver": 22.0,
        "certification.bronze": 16.0,
        "certified": 20.0,
    }
    _BUSINESS_SCHEMA_WEIGHTS: ClassVar[dict[str, float]] = {
        "dm": 16.0,
        "mart": 14.0,
        "datamart": 14.0,
        "semantic": 12.0,
    }
    _TECHNICAL_SCHEMA_PENALTIES: ClassVar[dict[str, float]] = {
        "stg": 24.0,
        "stage": 24.0,
        "staging": 24.0,
        "ods": 15.0,
        "dds": 9.0,
        "raw": 20.0,
        "landing": 18.0,
        "src": 16.0,
    }
    _SERVICE_ENGINE_WEIGHTS: ClassVar[dict[str, float]] = {
        "iceberg": 12.0,
        "hive": -6.0,
    }
    _CANONICAL_VARIANT_PENALTY: ClassVar[float] = 16.0

    def __init__(
        self,
        *,
        payload_mapper: MetadataPayloadMapper | None = None,
        semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        usage_store: MetadataUsageStore | None = None,
        candidate_generator: MetadataTableCandidateGenerator | None = None,
    ) -> None:
        self._payload_mapper = payload_mapper or MetadataPayloadMapper()
        self._semantic_grounding_service = (
            semantic_grounding_service or MetadataSemanticGroundingService()
        )
        self._usage_store = usage_store or InMemoryMetadataUsageStore()
        self._candidate_generator = candidate_generator or MetadataTableCandidateGenerator()
        self._logger = logging.getLogger(__name__)

    def filter_tables(
        self,
        *,
        tables: list[TableContext],
        search: str | None,
        service: str | None,
        database: str | None,
        schema: str | None,
        workspace_id: str | None = None,
        connector_name: str | None = None,
        top_n: int = 20,
        preferred_service: str | None = None,
        preferred_database: str | None = None,
        preferred_schema: str | None = None,
    ) -> list[TableContext]:
        normalized_search = search.strip().lower() if search else None
        search_terms = [token for token in (normalized_search or "").split() if token]
        usage_map = self._usage_store.usage_map(
            workspace_id=workspace_id,
            connector_name=connector_name,
        )
        zero_relevance_count = 0
        self._logger.info(
            "metadata.table_ranking.started",
            extra={
                "workspace_id": workspace_id,
                "connector_name": connector_name,
                "search": search,
                "service_filter": service,
                "database_filter": database,
                "schema_filter": schema,
                "preferred_service": preferred_service,
                "preferred_database": preferred_database,
                "preferred_schema": preferred_schema,
                "candidate_count": len(tables),
                "top_n": top_n,
            },
        )

        candidates, scope_filtered_count = self._candidate_generator.generate(
            tables=tables,
            service=service,
            database=database,
            schema=schema,
            usage_map=usage_map,
            logger=self._logger,
        )
        ranked: list[TableContext] = []
        for candidate in candidates:
            table = candidate.table
            usage_frequency = candidate.usage_frequency
            score, matched_on, score_breakdown = self._table_score_details(
                table=table,
                normalized_search=normalized_search,
                search_terms=search_terms,
                usage_frequency=usage_frequency,
                preferred_service=preferred_service,
                preferred_database=preferred_database,
                preferred_schema=preferred_schema,
            )
            if normalized_search and score <= 0:
                zero_relevance_count += 1
                self._logger.debug(
                    "metadata.table_ranking.candidate_skipped",
                    extra={
                        "table_fqn": table.fqn,
                        "reason": "zero_relevance",
                        "score": round(score, 3),
                        "matched_on": matched_on,
                        "score_breakdown": self._round_score_breakdown(score_breakdown),
                    },
                )
                continue
            self._logger.debug(
                "metadata.table_ranking.candidate_scored",
                extra={
                    "table_fqn": table.fqn,
                    "score": round(score, 3),
                    "matched_on": matched_on,
                    "usage_frequency": usage_frequency,
                    "score_breakdown": self._round_score_breakdown(score_breakdown),
                },
            )
            ranked.append(
                TableContext(
                    source=table.source,
                    fqn=table.fqn,
                    columns=list(table.columns),
                    column_types=dict(table.column_types),
                    column_descriptions=dict(table.column_descriptions),
                    description=table.description,
                    service=table.service,
                    database=table.database,
                    schema=table.schema,
                    owners=list(table.owners),
                    owner_refs=list(table.owner_refs),
                    owner_teams=list(table.owner_teams),
                    tags=list(table.tags),
                    domains=list(table.domains),
                    domain_refs=list(table.domain_refs),
                    glossary_terms=list(table.glossary_terms),
                    related_metrics=list(table.related_metrics),
                    quality_summary=table.quality_summary,
                    tier=table.tier,
                    certification=table.certification,
                    usage_count=table.usage_count,
                    relevance_score=score,
                    matched_on=matched_on,
                    usage_frequency=usage_frequency,
                )
            )

        self._apply_canonical_table_penalties(ranked)
        ranked.sort(
            key=lambda row: self._table_sort_key(table=row, search=normalized_search),
            reverse=True,
        )
        limited = ranked[: max(1, top_n)]
        self._logger.info(
            "metadata.table_ranking.completed",
            extra={
                "workspace_id": workspace_id,
                "connector_name": connector_name,
                "search": search,
                "candidate_count": len(tables),
                "scope_filtered_count": scope_filtered_count,
                "zero_relevance_count": zero_relevance_count,
                "ranked_count": len(ranked),
                "selected_count": len(limited),
                "selected_tables": [
                    {
                        "fqn": table.fqn,
                        "score": round(table.relevance_score, 3),
                        "matched_on": table.matched_on,
                        "usage_frequency": table.usage_frequency,
                        "certification": table.certification,
                        "tier": table.tier,
                    }
                    for table in limited
                ],
            },
        )
        return limited

    @classmethod
    def _table_sort_key(
        cls,
        *,
        table: TableContext,
        search: str | None,
    ) -> tuple[int, float, float, str]:
        identifier_rank = cls._identifier_match_rank(
            search=search,
            fqn=table.fqn,
            display_name=None,
        )
        return (identifier_rank, table.relevance_score, table.usage_frequency, table.fqn)

    def record_successful_usage(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None:
        """Record explicit successful table usage after grounded execution."""

        self._usage_store.record_successful_usage(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=table_fqns,
            occurred_at=occurred_at,
        )

    def record_negative_feedback(
        self,
        *,
        workspace_id: str | None,
        connector_name: str | None,
        table_fqns: list[str],
        occurred_at: datetime | None = None,
    ) -> None:
        """Record explicit negative table feedback for future ranking."""

        self._usage_store.record_negative_feedback(
            workspace_id=workspace_id,
            connector_name=connector_name,
            table_fqns=table_fqns,
            occurred_at=occurred_at,
        )

    @staticmethod
    def is_identifier_query(query: str | None) -> bool:
        if not isinstance(query, str) or not query.strip():
            return False
        return bool(re.fullmatch(r"[a-z0-9_.]+", query.strip().lower()))

    @classmethod
    def identifier_match_kind(
        cls,
        *,
        search: str | None,
        fqn: str | None,
        display_name: str | None = None,
    ) -> str:
        if not cls.is_identifier_query(search):
            return "none"
        normalized = str(search).strip().lower()
        candidate_names = [
            value.strip().lower()
            for value in (display_name, cls._leaf_name(fqn))
            if isinstance(value, str) and value.strip()
        ]
        if normalized in candidate_names:
            return "exact_leaf"
        if any(name.startswith(normalized) for name in candidate_names):
            return "prefix_leaf"
        if any(normalized in name for name in candidate_names):
            return "partial_leaf"
        fqn_lower = fqn.strip().lower() if isinstance(fqn, str) else ""
        if fqn_lower and normalized in fqn_lower:
            return "fqn_contains"
        return "none"

    @classmethod
    def _identifier_match_rank(
        cls,
        *,
        search: str | None,
        fqn: str | None,
        display_name: str | None = None,
    ) -> int:
        return cls._IDENTIFIER_MATCH_ORDER[
            cls.identifier_match_kind(
                search=search,
                fqn=fqn,
                display_name=display_name,
            )
        ]

    @classmethod
    def _table_score_details(
        cls,
        *,
        table: TableContext,
        normalized_search: str | None,
        search_terms: list[str],
        usage_frequency: float,
        preferred_service: str | None,
        preferred_database: str | None,
        preferred_schema: str | None,
    ) -> tuple[float, list[str], dict[str, float]]:
        score_breakdown: dict[str, float] = {}

        def _bump(component: str, delta: float) -> None:
            if delta == 0:
                return
            score_breakdown[component] = score_breakdown.get(component, 0.0) + delta

        if not normalized_search:
            score = max(-20.0, min(usage_frequency, 20.0))
            _bump("usage_prior", score)
            matched_on = ["usage_prior"] if usage_frequency != 0 else []
            governance_score = cls._governance_score(table=table, matched_on=matched_on)
            _bump("governance", governance_score)
            score += governance_score
            preferred_scope_score = cls._preferred_scope_score(
                table=table,
                matched_on=matched_on,
                preferred_service=preferred_service,
                preferred_database=preferred_database,
                preferred_schema=preferred_schema,
            )
            _bump("preferred_scope", preferred_scope_score)
            score += preferred_scope_score
            suspicious_scope_penalty = cls._suspicious_scope_penalty(
                table=table,
                normalized_search=None,
                search_terms=[],
                matched_on=matched_on,
            )
            _bump("suspicious_scope_penalty", -suspicious_scope_penalty)
            score -= suspicious_scope_penalty
            return score, matched_on, score_breakdown

        matched_on: list[str] = []
        score = 0.0
        has_search_evidence = False
        fqn = table.fqn.lower()
        fqn_leaf = fqn.split(".")[-1] if "." in fqn else fqn
        schema_lower = table.schema.lower() if isinstance(table.schema, str) else ""
        database_lower = table.database.lower() if isinstance(table.database, str) else ""
        service_lower = table.service.lower() if isinstance(table.service, str) else ""
        table_description = table.description.lower() if isinstance(table.description, str) else ""

        if normalized_search == fqn_leaf:
            score += 120.0
            _bump("name_exact", 120.0)
            matched_on.append("name_exact")
            has_search_evidence = True
        elif normalized_search in fqn_leaf:
            score += 62.0
            _bump("name_partial", 62.0)
            matched_on.append("name_partial")
            has_search_evidence = True
        elif normalized_search in fqn:
            score += 42.0
            _bump("fqn_partial", 42.0)
            matched_on.append("fqn_partial")
            has_search_evidence = True

        columns_lower = [column.lower() for column in table.columns]
        column_descriptions = {
            name.lower(): description.lower()
            for name, description in table.column_descriptions.items()
            if isinstance(description, str)
        }
        tags_lower = [tag.lower() for tag in table.tags]
        owners_lower = [owner.lower() for owner in table.owners]
        domains_lower = [domain.lower() for domain in table.domains]
        glossary_terms_lower = [
            value.lower()
            for ref in table.glossary_terms
            for value in (ref.name, ref.display_name, ref.fully_qualified_name)
            if isinstance(value, str) and value.strip()
        ]
        metrics_lower = [
            value.lower()
            for ref in table.related_metrics
            for value in (ref.name, ref.display_name, ref.fully_qualified_name, ref.description)
            if isinstance(value, str) and value.strip()
        ]
        for term in search_terms:
            if term == schema_lower:
                score += 18.0
                _bump("schema_exact", 18.0)
                if "schema_exact" not in matched_on:
                    matched_on.append("schema_exact")
                has_search_evidence = True
            elif schema_lower and term in schema_lower:
                score += 10.0
                _bump("schema_partial", 10.0)
                if "schema_partial" not in matched_on:
                    matched_on.append("schema_partial")
                has_search_evidence = True

            if term == database_lower:
                score += 12.0
                _bump("database_exact", 12.0)
                if "database_exact" not in matched_on:
                    matched_on.append("database_exact")
                has_search_evidence = True
            elif database_lower and term in database_lower:
                score += 7.0
                _bump("database_partial", 7.0)
                if "database_partial" not in matched_on:
                    matched_on.append("database_partial")
                has_search_evidence = True

            if term == service_lower:
                score += 8.0
                _bump("service_exact", 8.0)
                if "service_exact" not in matched_on:
                    matched_on.append("service_exact")
                has_search_evidence = True
            elif service_lower and term in service_lower:
                score += 5.0
                _bump("service_partial", 5.0)
                if "service_partial" not in matched_on:
                    matched_on.append("service_partial")
                has_search_evidence = True

            if term in columns_lower:
                score += 26.0
                _bump("column_exact", 26.0)
                if "column_exact" not in matched_on:
                    matched_on.append("column_exact")
                has_search_evidence = True
            elif any(term in column for column in columns_lower):
                score += 12.0
                _bump("column_partial", 12.0)
                if "column_partial" not in matched_on:
                    matched_on.append("column_partial")
                has_search_evidence = True

            if any(term in description for description in column_descriptions.values()):
                score += 12.0
                _bump("description_partial", 12.0)
                if "description_partial" not in matched_on:
                    matched_on.append("description_partial")
                has_search_evidence = True

            if table_description and term in table_description:
                score += 16.0
                _bump("table_description_partial", 16.0)
                if "table_description_partial" not in matched_on:
                    matched_on.append("table_description_partial")
                has_search_evidence = True

            if any(term in tag for tag in tags_lower):
                score += 5.0
                _bump("tag_partial", 5.0)
                if "tag_partial" not in matched_on:
                    matched_on.append("tag_partial")
                has_search_evidence = True

            if any(term in owner for owner in owners_lower):
                score += 4.0
                _bump("owner_partial", 4.0)
                if "owner_partial" not in matched_on:
                    matched_on.append("owner_partial")
                has_search_evidence = True

            if any(term in domain for domain in domains_lower):
                score += 5.0
                _bump("domain_partial", 5.0)
                if "domain_partial" not in matched_on:
                    matched_on.append("domain_partial")
                has_search_evidence = True

            if any(term in glossary for glossary in glossary_terms_lower):
                score += 18.0
                _bump("glossary_term_partial", 18.0)
                if "glossary_term_partial" not in matched_on:
                    matched_on.append("glossary_term_partial")
                has_search_evidence = True

            if any(term in metric for metric in metrics_lower):
                score += 24.0
                _bump("metric_partial", 24.0)
                if "metric_partial" not in matched_on:
                    matched_on.append("metric_partial")
                has_search_evidence = True

        if not has_search_evidence:
            return 0.0, matched_on, score_breakdown

        governance_score = cls._governance_score(table=table, matched_on=matched_on)
        _bump("governance", governance_score)
        score += governance_score
        preferred_scope_score = cls._preferred_scope_score(
            table=table,
            matched_on=matched_on,
            preferred_service=preferred_service,
            preferred_database=preferred_database,
            preferred_schema=preferred_schema,
        )
        _bump("preferred_scope", preferred_scope_score)
        score += preferred_scope_score
        suspicious_scope_penalty = cls._suspicious_scope_penalty(
            table=table,
            normalized_search=normalized_search,
            search_terms=search_terms,
            matched_on=matched_on,
        )
        _bump("suspicious_scope_penalty", -suspicious_scope_penalty)
        score -= suspicious_scope_penalty

        if usage_frequency != 0:
            usage_prior = max(-8.0, min(usage_frequency, 15.0) * 0.5)
            _bump("usage_prior", usage_prior)
            score += usage_prior
            matched_on.append("usage_prior")
        return score, matched_on, score_breakdown

    @staticmethod
    def _round_score_breakdown(score_breakdown: dict[str, float]) -> dict[str, float]:
        return {
            component: round(value, 3)
            for component, value in score_breakdown.items()
            if value != 0
        }

    @staticmethod
    def _leaf_name(value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip().lower().split(".")[-1]

    @classmethod
    def _apply_canonical_table_penalties(cls, tables: list[TableContext]) -> None:
        leaves = {
            cls._leaf_name(table.fqn)
            for table in tables
            if isinstance(table.fqn, str) and table.fqn.strip()
        }
        for table in tables:
            leaf = cls._leaf_name(table.fqn)
            if not leaf:
                continue
            if any(leaf != candidate and leaf.startswith(f"{candidate}_") for candidate in leaves):
                table.relevance_score -= cls._CANONICAL_VARIANT_PENALTY
                if "specialized_variant_penalty" not in table.matched_on:
                    table.matched_on.append("specialized_variant_penalty")

    @classmethod
    def _governance_score(
        cls,
        *,
        table: TableContext,
        matched_on: list[str],
    ) -> float:
        score = 0.0
        certification = (table.certification or "").strip().lower()
        if certification:
            score += cls._CERTIFICATION_WEIGHTS.get(certification, 18.0)
            matched_on.append("certification")
        tier = (table.tier or "").strip().lower()
        if tier:
            score += cls._TIER_WEIGHTS.get(tier, 8.0)
            matched_on.append("tier")
        score += cls._architecture_layer_score(table=table, matched_on=matched_on)
        score += cls._service_engine_score(table=table, matched_on=matched_on)
        if table.domains:
            score += 4.0
            matched_on.append("domain_present")
        if table.domain_refs:
            score += 3.0
            matched_on.append("domain_ref_present")
        if table.glossary_terms:
            score += 8.0
            matched_on.append("glossary_term_present")
        if table.related_metrics:
            score += 10.0
            matched_on.append("metric_present")
        if table.owners:
            score += 2.5
            matched_on.append("owner_present")
        if table.owner_teams:
            score += 3.0
            matched_on.append("team_owner_present")
        if table.description:
            score += 7.0
            matched_on.append("table_described")
        if table.usage_count > 0:
            score += min(18.0, 4.0 + min(table.usage_count, 200) / 12.0)
            matched_on.append("metadata_usage")
        if table.quality_summary is not None:
            quality = table.quality_summary
            if quality.status == "success":
                score += 4.0
                matched_on.append("quality_success")
            elif quality.status == "failed":
                score -= 28.0
                matched_on.append("quality_failed")
            elif quality.status == "aborted":
                score -= 14.0
                matched_on.append("quality_aborted")
        return score

    @classmethod
    def _architecture_layer_score(
        cls,
        *,
        table: TableContext,
        matched_on: list[str],
    ) -> float:
        schema = (table.schema or "").strip().lower()
        if not schema:
            return 0.0
        business_schema = cls._schema_family_match(
            schema=schema,
            known_families=cls._BUSINESS_SCHEMA_WEIGHTS,
        )
        if business_schema is not None:
            matched_on.append("business_schema")
            return cls._BUSINESS_SCHEMA_WEIGHTS[business_schema]
        technical_schema = cls._schema_family_match(
            schema=schema,
            known_families=cls._TECHNICAL_SCHEMA_PENALTIES,
        )
        if technical_schema is not None:
            matched_on.append("technical_schema")
            return -cls._TECHNICAL_SCHEMA_PENALTIES[technical_schema]
        return 0.0

    @staticmethod
    def _schema_family_match(
        *,
        schema: str,
        known_families: dict[str, float],
    ) -> str | None:
        for family in known_families:
            if schema == family:
                return family
            tokens = [token for token in re.split(r"[^a-z0-9]+", schema) if token]
            if family in tokens:
                return family
        return None

    @classmethod
    def _service_engine_score(
        cls,
        *,
        table: TableContext,
        matched_on: list[str],
    ) -> float:
        scope_values = [
            value.strip().lower()
            for value in (table.service, table.database, table.schema, table.fqn)
            if isinstance(value, str) and value.strip()
        ]
        if any("iceberg" in value for value in scope_values):
            matched_on.append("engine_iceberg")
            return cls._SERVICE_ENGINE_WEIGHTS["iceberg"]
        if any("hive" in value for value in scope_values):
            matched_on.append("engine_hive")
            return cls._SERVICE_ENGINE_WEIGHTS["hive"]
        return 0.0

    @staticmethod
    def _preferred_scope_score(
        *,
        table: TableContext,
        matched_on: list[str],
        preferred_service: str | None,
        preferred_database: str | None,
        preferred_schema: str | None,
    ) -> float:
        score = 0.0
        comparisons = (
            ("service", preferred_service, table.service, 7.0, 4.0),
            ("database", preferred_database, table.database, 10.0, 6.0),
            ("schema", preferred_schema, table.schema, 14.0, 10.0),
        )
        for label, preferred, actual, boost, penalty in comparisons:
            if not isinstance(preferred, str) or not preferred.strip():
                continue
            if not isinstance(actual, str) or not actual.strip():
                score -= penalty / 2
                matched_on.append(f"preferred_{label}_missing")
                continue
            if actual.strip().lower() == preferred.strip().lower():
                score += boost
                matched_on.append(f"preferred_{label}")
            else:
                score -= penalty
                matched_on.append(f"nonpreferred_{label}")
        return score

    @classmethod
    def _suspicious_scope_penalty(
        cls,
        *,
        table: TableContext,
        normalized_search: str | None,
        search_terms: list[str],
        matched_on: list[str],
    ) -> float:
        search_space = " ".join(filter(None, [normalized_search, *search_terms]))
        suspicious_tokens = MetadataSemanticGroundingService.SUSPICIOUS_SCOPE_TOKENS
        if any(token in search_space for token in suspicious_tokens):
            return 0.0

        scope_values = (table.fqn, table.service, table.database, table.schema)
        suspicious_hits = {
            token
            for token in suspicious_tokens
            if any(isinstance(value, str) and token in value.lower() for value in scope_values)
        }
        if not suspicious_hits:
            return 0.0
        matched_on.append("suspicious_scope_penalty")
        return 18.0 + (len(suspicious_hits) - 1) * 6.0

    def summarize_catalog_result(
        self,
        *,
        item: dict[str, Any],
        query: str,
        preferred_service: str | None,
        preferred_database: str | None,
        preferred_schema: str | None,
    ) -> dict[str, Any]:
        entity_type = MetadataCatalogGateway.normalize_entity_type(item.get("entityType"))
        if entity_type is None:
            entity_type = self._payload_mapper.to_optional_str(item.get("entityType"))
        fqn = self._payload_mapper.to_optional_str(
            item.get("fullyQualifiedName") or item.get("fqn") or item.get("name")
        )
        display_name = self._payload_mapper.to_optional_str(item.get("displayName")) or self._payload_mapper.to_optional_str(item.get("name"))
        if display_name is None and fqn is not None:
            display_name = fqn.split(".")[-1]

        column_names = item.get("columnNames", []) if isinstance(item.get("columnNames"), list) else []
        tags = self._payload_mapper.extract_tag_fqns(item.get("tags"))
        owners = self._payload_mapper.extract_entity_names(item.get("owners"))
        owner_refs = self._payload_mapper.extract_entity_refs(
            item.get("owner_refs") or item.get("owners")
        )
        owner_teams = [
            str(value).strip()
            for value in item.get("owner_teams", [])
            if isinstance(value, str) and value.strip()
        ] or [
            ref.name
            for ref in owner_refs
            if (ref.type or "").strip().lower() == "team"
        ]
        domains = self._payload_mapper.extract_entity_names(item.get("domains") or item.get("domain"))
        domain_refs = self._payload_mapper.extract_entity_refs(
            item.get("domain_refs") or item.get("domains") or item.get("domain")
        )
        glossary_terms = self._payload_mapper.extract_entity_refs(
            item.get("glossary_terms") or item.get("glossaryTerms")
        ) or self._payload_mapper.extract_glossary_terms(item)
        related_metrics = self._payload_mapper.extract_metric_refs(
            item.get("related_metrics") or item.get("relatedMetrics") or item.get("metrics")
        )
        quality_summary = self._payload_mapper.extract_quality_summary(item.get("quality_summary"))
        description = self._payload_mapper.to_optional_str(item.get("description"))
        service = self._payload_mapper.extract_nested_name(item.get("service")) or self._payload_mapper.to_optional_str(item.get("service"))
        database = self._payload_mapper.extract_nested_name(item.get("database")) or self._payload_mapper.to_optional_str(item.get("database"))
        database_schema = self._payload_mapper.extract_nested_name(item.get("databaseSchema"))
        database_schema = database_schema or self._payload_mapper.to_optional_str(item.get("schema"))
        database_schema = database_schema or self._payload_mapper.extract_nested_name(item.get("schema"))

        relevance_score = 0.0
        matched_on: list[str] = []
        reason_codes: list[str] = []
        suspicious = False
        if entity_type == "table" and fqn:
            tier = self._payload_mapper.extract_tier_from_payload(item.get("tier"), tags)
            certification = self._payload_mapper.extract_certification_from_payload(
                item.get("certification"),
                tags,
            )
            table_context = TableContext(
                source="mcp",
                fqn=fqn,
                columns=[str(column).strip() for column in column_names[:20] if str(column).strip()],
                description=description,
                service=service,
                database=database,
                schema=database_schema,
                owners=owners,
                owner_refs=owner_refs,
                owner_teams=owner_teams,
                tags=tags,
                domains=domains,
                domain_refs=domain_refs,
                glossary_terms=glossary_terms,
                related_metrics=related_metrics,
                quality_summary=quality_summary,
                tier=tier,
                certification=certification,
                usage_count=self._payload_mapper.extract_usage_count(item.get("usageSummary")),
            )
            normalized_query = query.strip().lower()
            search_terms = [token for token in normalized_query.split() if token]
            relevance_score, matched_on, score_breakdown = self._table_score_details(
                table=table_context,
                normalized_search=normalized_query,
                search_terms=search_terms,
                usage_frequency=0,
                preferred_service=preferred_service,
                preferred_database=preferred_database,
                preferred_schema=preferred_schema,
            )
            self._logger.debug(
                "metadata.catalog_ranking.candidate_scored",
                extra={
                    "query": query,
                    "table_fqn": fqn,
                    "score": round(relevance_score, 3),
                    "matched_on": matched_on,
                    "score_breakdown": self._round_score_breakdown(score_breakdown),
                },
            )
            scored_table = TableContext(
                source=table_context.source,
                fqn=table_context.fqn,
                columns=list(table_context.columns),
                description=table_context.description,
                service=table_context.service,
                database=table_context.database,
                schema=table_context.schema,
                owners=list(table_context.owners),
                owner_refs=list(table_context.owner_refs),
                owner_teams=list(table_context.owner_teams),
                tags=list(table_context.tags),
                domains=list(table_context.domains),
                domain_refs=list(table_context.domain_refs),
                glossary_terms=list(table_context.glossary_terms),
                related_metrics=list(table_context.related_metrics),
                quality_summary=table_context.quality_summary,
                tier=table_context.tier,
                certification=table_context.certification,
                usage_count=table_context.usage_count,
                relevance_score=relevance_score,
                matched_on=matched_on,
            )
            reason_codes = self._semantic_grounding_service.semantic_reason_codes(scored_table)
            suspicious = self._semantic_grounding_service.is_suspicious_table(scored_table)
        else:
            relevance_score, matched_on = self._generic_entity_score_details(
                display_name=display_name,
                fqn=fqn,
                description=description,
                query=query,
            )

        identifier_match = self.identifier_match_kind(
            search=query,
            fqn=fqn,
            display_name=display_name,
        )
        if identifier_match != "none" and identifier_match not in matched_on:
            matched_on.append(identifier_match)

        return {
            "entityType": entity_type,
            "displayName": display_name,
            "fullyQualifiedName": fqn,
            "description": description,
            "service": service,
            "database": database,
            "databaseSchema": database_schema,
            "columnNames": [str(column).strip() for column in column_names[:20] if str(column).strip()],
            "owners": owners[:10],
            "owner_teams": owner_teams[:10],
            "owner_refs": [
                self._payload_mapper.entity_ref_payload(ref)
                for ref in owner_refs[:10]
            ],
            "tags": tags[:10],
            "domains": domains[:10],
            "domain_refs": [
                self._payload_mapper.entity_ref_payload(ref)
                for ref in domain_refs[:10]
            ],
            "glossary_terms": [
                self._payload_mapper.entity_ref_payload(ref)
                for ref in glossary_terms[:10]
            ],
            "related_metrics": [
                self._payload_mapper.metric_ref_payload(ref)
                for ref in related_metrics[:10]
            ],
            "quality_summary": (
                quality_summary.to_payload() if quality_summary is not None else None
            ),
            "quality_status": quality_summary.status if quality_summary is not None else None,
            "tier": self._payload_mapper.extract_tier_from_payload(item.get("tier"), tags),
            "certification": self._payload_mapper.extract_certification_from_payload(
                item.get("certification"),
                tags,
            ),
            "identifier_match": identifier_match,
            "usage_count": self._payload_mapper.extract_usage_count(item.get("usageSummary")),
            "relevance_score": round(relevance_score, 3),
            "matched_on": matched_on,
            "reason_codes": reason_codes,
            "suspicious": suspicious,
        }

    @staticmethod
    def _generic_entity_score_details(
        *,
        display_name: str | None,
        fqn: str | None,
        description: str | None,
        query: str,
    ) -> tuple[float, list[str]]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return 0.0, []

        score = 0.0
        matched_on: list[str] = []
        display_lower = display_name.lower() if isinstance(display_name, str) else ""
        fqn_lower = fqn.lower() if isinstance(fqn, str) else ""
        description_lower = description.lower() if isinstance(description, str) else ""

        if display_lower == normalized_query:
            score += 90.0
            matched_on.append("name_exact")
        elif normalized_query in display_lower:
            score += 46.0
            matched_on.append("name_partial")
        elif normalized_query in fqn_lower:
            score += 28.0
            matched_on.append("fqn_partial")

        for term in [token for token in normalized_query.split() if token]:
            if description_lower and term in description_lower:
                score += 8.0
                if "description_partial" not in matched_on:
                    matched_on.append("description_partial")
        return score, matched_on

    @staticmethod
    def catalog_result_identity(row: dict[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("entityType") or "").strip().lower(),
            str(
                row.get("fullyQualifiedName")
                or row.get("displayName")
                or row.get("name")
                or ""
            ).strip(),
        )

    @classmethod
    def apply_canonical_catalog_penalties(cls, rows: list[dict[str, Any]]) -> None:
        leaves = {
            cls._leaf_name(str(row.get("fullyQualifiedName") or ""))
            for row in rows
            if str(row.get("entityType") or "").strip().lower() == "table"
            and row.get("fullyQualifiedName")
        }
        for row in rows:
            if str(row.get("entityType") or "").strip().lower() != "table":
                continue
            leaf = cls._leaf_name(str(row.get("fullyQualifiedName") or ""))
            if not leaf:
                continue
            if any(leaf != candidate and leaf.startswith(f"{candidate}_") for candidate in leaves):
                row["relevance_score"] = round(
                    float(row.get("relevance_score", 0.0)) - cls._CANONICAL_VARIANT_PENALTY,
                    3,
                )
                matched_on = row.get("matched_on")
                if not isinstance(matched_on, list):
                    matched_on = []
                if "specialized_variant_penalty" not in matched_on:
                    matched_on.append("specialized_variant_penalty")
                row["matched_on"] = matched_on

    @classmethod
    def catalog_sort_key(
        cls,
        *,
        row: dict[str, Any],
        query: str | None,
    ) -> tuple[int, float, int, str]:
        identifier_match = str(
            row.get("identifier_match")
            or cls.identifier_match_kind(
                search=query,
                fqn=str(row.get("fullyQualifiedName") or ""),
                display_name=str(row.get("displayName") or ""),
            )
        )
        return (
            cls._IDENTIFIER_MATCH_ORDER.get(identifier_match, 0),
            float(row.get("relevance_score", 0.0)),
            int(row.get("usage_count", 0)),
            str(row.get("fullyQualifiedName") or ""),
        )

    @staticmethod
    def catalog_candidate_pool_size(
        *,
        entity_type: str | None,
        requested_size: int,
    ) -> int:
        multiplier = 4 if entity_type in (None, "table") else 2
        minimum = 40 if entity_type in (None, "table") else requested_size
        maximum = 120 if entity_type in (None, "table") else 50
        return min(max(requested_size * multiplier, minimum), maximum)

    def merge_catalog_results(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            identity = self.catalog_result_identity(row)
            existing = merged.get(identity)
            if existing is None:
                merged[identity] = dict(row)
                continue
            candidate = dict(row) if float(row.get("relevance_score", 0.0)) > float(
                existing.get("relevance_score", 0.0)
            ) else dict(existing)
            merged_matched_on: list[str] = []
            for source in (existing.get("matched_on"), row.get("matched_on")):
                if not isinstance(source, list):
                    continue
                for item in source:
                    if isinstance(item, str) and item not in merged_matched_on:
                        merged_matched_on.append(item)
            candidate["matched_on"] = merged_matched_on
            candidate["usage_count"] = max(
                int(existing.get("usage_count", 0)),
                int(row.get("usage_count", 0)),
            )
            merged[identity] = candidate
        return list(merged.values())
