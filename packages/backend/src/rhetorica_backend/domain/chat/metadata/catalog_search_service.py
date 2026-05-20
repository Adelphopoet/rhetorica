"""Catalog search orchestration built from smaller metadata collaborators."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.catalog_semantic_expansion_service import (
    MetadataCatalogSemanticExpansionService,
)
from rhetorica_backend.domain.chat.metadata.governance_enrichment_service import (
    OpenMetadataGovernanceEnrichmentService,
)
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService


class MetadataCatalogSearchService:
    """Search metadata catalog and expand related semantic entities."""

    def __init__(
        self,
        *,
        catalog_gateway: MetadataCatalogGateway | None = None,
        payload_mapper: MetadataPayloadMapper | None = None,
        ranking_service: MetadataRankingService | None = None,
        semantic_expansion_service: MetadataCatalogSemanticExpansionService | None = None,
        governance_enrichment_service: OpenMetadataGovernanceEnrichmentService | None = None,
    ) -> None:
        self._catalog_gateway = catalog_gateway or MetadataCatalogGateway()
        self._payload_mapper = payload_mapper or MetadataPayloadMapper()
        self._ranking_service = ranking_service or MetadataRankingService(
            payload_mapper=self._payload_mapper
        )
        self._semantic_expansion_service = (
            semantic_expansion_service
            or MetadataCatalogSemanticExpansionService(
                catalog_gateway=self._catalog_gateway,
                payload_mapper=self._payload_mapper,
                ranking_service=self._ranking_service,
            )
        )
        self._governance_enrichment_service = (
            governance_enrichment_service
            or OpenMetadataGovernanceEnrichmentService(payload_mapper=self._payload_mapper)
        )
        self._logger = logging.getLogger(__name__)

    def search_catalog(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        results, warnings, _ranked_results = self.search_catalog_with_ranked_results(
            connection=connection,
            query=query,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
            service=service,
            database=database,
            schema=schema,
        )
        return results, warnings

    def search_catalog_with_ranked_results(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        warnings: list[str] = []
        if not connection.mcp_tables_url:
            warnings.append("metadata_mcp_unconfigured")
            return [], warnings, []

        normalized_entity_type = self._catalog_gateway.normalize_entity_type(entity_type)
        size = self._catalog_gateway.bounded_int(limit, default=20, minimum=1, maximum=50)
        safe_offset = self._catalog_gateway.bounded_int(
            offset,
            default=0,
            minimum=0,
            maximum=10_000,
        )
        try:
            results = self._catalog_gateway.search_raw_results(
                connection=connection,
                query=query,
                entity_type=normalized_entity_type,
                limit=self._ranking_service.catalog_candidate_pool_size(
                    entity_type=normalized_entity_type,
                    requested_size=size,
                ),
                offset=safe_offset,
                service=service,
                database=database,
                schema=schema,
            )
        except httpx.HTTPStatusError as exc:
            warnings.append(f"metadata_mcp_http_{exc.response.status_code}")
            return [], warnings, []
        except httpx.HTTPError:
            warnings.append("metadata_mcp_unreachable")
            return [], warnings, []
        except Exception:
            warnings.append("metadata_mcp_failed")
            return [], warnings, []

        summarized_results = [
            self._ranking_service.summarize_catalog_result(
                item=item,
                query=query,
                preferred_service=connection.service_name,
                preferred_database=connection.database_name,
                preferred_schema=connection.schema_name,
            )
            for item in results
        ]
        if normalized_entity_type in (None, "table") and not self._ranking_service.is_identifier_query(query):
            related_rows, related_warnings = self._semantic_expansion_service.expand_related_tables(
                connection=connection,
                query=query,
                preferred_service=connection.service_name,
                preferred_database=connection.database_name,
                preferred_schema=connection.schema_name,
                requested_size=size,
            )
            for warning in related_warnings:
                if warning not in warnings:
                    warnings.append(warning)
            summarized_results.extend(related_rows)

        summarized_results = self._ranking_service.merge_catalog_results(summarized_results)
        enriched_results, enrichment_warnings = (
            self._governance_enrichment_service.enrich_catalog_rows(
                connection=connection,
                rows=summarized_results,
            )
        )
        for warning in enrichment_warnings:
            if warning not in warnings:
                warnings.append(warning)
        summarized_results = self._apply_quality_scores(enriched_results)
        self._ranking_service.apply_canonical_catalog_penalties(summarized_results)
        summarized_results.sort(
            key=lambda row: self._ranking_service.catalog_sort_key(row=row, query=query),
            reverse=True,
        )
        ranked_results: list[dict[str, Any]] = []
        for rank, row in enumerate(summarized_results, start=1):
            row["rank"] = rank
            ranked_results.append(self._compact_ranked_result(row))

        selected_results = list(summarized_results)
        if normalized_entity_type in (None, "table"):
            positive_table_scores = [
                float(row.get("relevance_score", 0.0))
                for row in selected_results
                if str(row.get("entityType") or "").strip().lower() == "table"
                and float(row.get("relevance_score", 0.0)) > 0
            ]
            if positive_table_scores:
                selected_results = [
                    row
                    for row in selected_results
                    if str(row.get("entityType") or "").strip().lower() != "table"
                    or float(row.get("relevance_score", 0.0)) > 0
                ]
        if normalized_entity_type == "table" and self._ranking_service.is_identifier_query(query):
            selected_results = [
                row
                for row in selected_results
                if float(row.get("relevance_score", 0.0)) > 0
            ]

        limited_results = selected_results[:size]
        self._logger.info(
            "metadata.catalog_ranking.completed",
            extra={
                "query": query,
                "entity_type": normalized_entity_type,
                "result_count": len(results),
                "ranked_result_count": len(ranked_results),
                "selected_count": len(limited_results),
                "selected_results": [
                    {
                        "rank": row.get("rank"),
                        "fqn": row.get("fullyQualifiedName"),
                        "entity_type": row.get("entityType"),
                        "score": row.get("relevance_score"),
                        "matched_on": row.get("matched_on"),
                    }
                    for row in limited_results
                ],
                "warnings": warnings,
            },
        )
        return limited_results, warnings, ranked_results

    @staticmethod
    def _compact_ranked_result(row: dict[str, Any]) -> dict[str, Any]:
        """Token-cheap catalog ranking item for LLM-side source selection."""

        compact: dict[str, Any] = {}
        for key in (
            "rank",
            "entityType",
            "fullyQualifiedName",
            "displayName",
            "description",
            "service",
            "database",
            "databaseSchema",
            "relevance_score",
            "identifier_match",
            "certification",
            "tier",
            "suspicious",
        ):
            value = row.get(key)
            if isinstance(value, bool):
                if value:
                    compact[key] = value
                continue
            if value not in (None, "", [], {}):
                compact[key] = value

        for key in ("matched_on", "reason_codes"):
            value = row.get(key)
            if isinstance(value, list) and value:
                compact[key] = value[:6]
        return compact

    @staticmethod
    def _apply_quality_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        adjusted: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            quality = item.get("quality_summary")
            if isinstance(quality, dict):
                matched_on = item.get("matched_on")
                if not isinstance(matched_on, list):
                    matched_on = []
                reason_codes = item.get("reason_codes")
                if not isinstance(reason_codes, list):
                    reason_codes = []
                score = float(item.get("relevance_score") or 0.0)
                if int(quality.get("failed") or 0) > 0:
                    score -= 28.0
                    if "quality_failed" not in matched_on:
                        matched_on.append("quality_failed")
                    if "quality_failed" not in reason_codes:
                        reason_codes.append("quality_failed")
                elif int(quality.get("aborted") or 0) > 0:
                    score -= 14.0
                    if "quality_aborted" not in matched_on:
                        matched_on.append("quality_aborted")
                    if "quality_aborted" not in reason_codes:
                        reason_codes.append("quality_aborted")
                elif str(quality.get("status") or "").lower() == "success":
                    score += 4.0
                    if "quality_success" not in matched_on:
                        matched_on.append("quality_success")
                item["relevance_score"] = round(score, 3)
                item["matched_on"] = matched_on
                item["reason_codes"] = reason_codes
            adjusted.append(item)
        return adjusted

    def get_entity_details(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        result, warnings = self._catalog_gateway.get_entity_details(
            connection=connection,
            entity_type=entity_type,
            fqn=fqn,
        )
        enriched_result, enrichment_warnings = (
            self._governance_enrichment_service.enrich_entity_details(
                connection=connection,
                entity=result,
            )
        )
        return enriched_result, [*warnings, *enrichment_warnings]

    def get_entity_lineage(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        return self._catalog_gateway.get_entity_lineage(
            connection=connection,
            entity_type=entity_type,
            fqn=fqn,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
        )
