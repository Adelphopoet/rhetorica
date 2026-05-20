"""Expand catalog search results through related semantic entities."""

from __future__ import annotations

from typing import Any

import httpx
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService


class MetadataCatalogSemanticExpansionService:
    """Resolve related glossary and metric entities into ranked table candidates."""

    def __init__(
        self,
        *,
        catalog_gateway: MetadataCatalogGateway,
        payload_mapper: MetadataPayloadMapper,
        ranking_service: MetadataRankingService,
    ) -> None:
        self._catalog_gateway = catalog_gateway
        self._payload_mapper = payload_mapper
        self._ranking_service = ranking_service

    def expand_related_tables(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        query: str,
        preferred_service: str | None,
        preferred_database: str | None,
        preferred_schema: str | None,
        requested_size: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        derived: dict[str, dict[str, Any]] = {}

        for related_entity_type, relation_bonus in (("glossaryTerm", 18.0), ("metric", 26.0)):
            try:
                related_entities = self._catalog_gateway.search_raw_results(
                    connection=connection,
                    query=query,
                    entity_type=related_entity_type,
                    limit=min(max(requested_size, 5), 10),
                    offset=0,
                )
            except httpx.HTTPStatusError as exc:
                warnings.append(f"metadata_mcp_http_{exc.response.status_code}")
                continue
            except httpx.HTTPError:
                warnings.append("metadata_mcp_unreachable")
                continue
            except Exception:
                warnings.append("metadata_mcp_failed")
                continue

            for entity in related_entities[:10]:
                entity_fqn = self._payload_mapper.to_optional_str(
                    entity.get("fullyQualifiedName")
                    or entity.get("fqn")
                    or entity.get("name")
                )
                if not entity_fqn:
                    continue

                related_tables = []
                details, detail_warnings = self._catalog_gateway.get_entity_details(
                    connection=connection,
                    entity_type=related_entity_type,
                    fqn=entity_fqn,
                )
                for warning in detail_warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                if details is not None:
                    related_tables.extend(
                        self._payload_mapper.extract_related_table_contexts(
                            details,
                            normalize_entity_type=self._catalog_gateway.normalize_entity_type,
                        )
                    )

                if related_entity_type == "metric" or not related_tables:
                    lineage, lineage_warnings = self._catalog_gateway.get_entity_lineage(
                        connection=connection,
                        entity_type=related_entity_type,
                        fqn=entity_fqn,
                        upstream_depth=1,
                        downstream_depth=1,
                    )
                    for warning in lineage_warnings:
                        if warning not in warnings:
                            warnings.append(warning)
                    if lineage is not None:
                        related_tables.extend(
                            self._payload_mapper.extract_related_table_contexts(
                                lineage,
                                normalize_entity_type=self._catalog_gateway.normalize_entity_type,
                            )
                        )

                for table in related_tables:
                    summary = self._ranking_service.summarize_catalog_result(
                        item=self._payload_mapper.catalog_item_from_table(table),
                        query=query,
                        preferred_service=preferred_service,
                        preferred_database=preferred_database,
                        preferred_schema=preferred_schema,
                    )
                    summary["relevance_score"] = round(
                        float(summary.get("relevance_score", 0.0)) + relation_bonus,
                        3,
                    )
                    matched_on = summary.get("matched_on")
                    if not isinstance(matched_on, list):
                        matched_on = []
                    relation_marker = f"related_{related_entity_type.lower()}"
                    if relation_marker not in matched_on:
                        matched_on.append(relation_marker)
                    summary["matched_on"] = matched_on

                    identity = self._ranking_service.catalog_result_identity(summary)
                    existing = derived.get(identity[1])
                    if existing is None or float(summary["relevance_score"]) > float(
                        existing.get("relevance_score", 0.0)
                    ):
                        derived[identity[1]] = summary

        return list(derived.values()), warnings
