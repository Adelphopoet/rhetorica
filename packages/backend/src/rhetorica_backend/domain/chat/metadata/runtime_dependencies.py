"""Resolve explicit runtime metadata collaborators with a legacy fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.catalog_search_service import (
    MetadataCatalogSearchService,
)
from rhetorica_backend.domain.chat.metadata.catalog_semantic_expansion_service import (
    MetadataCatalogSemanticExpansionService,
)
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService
from rhetorica_backend.domain.chat.metadata.semantic_grounding_service import (
    MetadataSemanticGroundingService,
)
from rhetorica_backend.domain.chat.metadata.table_loader import MetadataTableLoader
from rhetorica_backend.domain.chat.metadata.usage_store import MetadataUsageStore

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.metadata.metadata_service import MetadataFetcher


@dataclass(frozen=True)
class MetadataRuntimeServices:
    """Resolved runtime collaborators used by chat orchestration."""

    payload_mapper: MetadataPayloadMapper
    catalog_gateway: MetadataCatalogGateway
    table_loader: Any | None
    ranking_service: Any | None
    catalog_search_service: Any | None
    semantic_grounding_service: MetadataSemanticGroundingService


def resolve_metadata_runtime_services(
    *,
    metadata_fetcher: MetadataFetcher | None = None,
    payload_mapper: MetadataPayloadMapper | None = None,
    catalog_gateway: MetadataCatalogGateway | None = None,
    metadata_table_loader: MetadataTableLoader | None = None,
    metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
    metadata_ranking_service: MetadataRankingService | None = None,
    metadata_semantic_grounding_service: MetadataSemanticGroundingService | None = None,
    metadata_usage_store: MetadataUsageStore | None = None,
) -> MetadataRuntimeServices:
    """Build direct metadata collaborators and bridge legacy fetcher stubs when needed."""

    resolved_payload_mapper = payload_mapper or getattr(
        metadata_fetcher, "payload_mapper", None
    )
    if resolved_payload_mapper is None:
        resolved_payload_mapper = MetadataPayloadMapper()

    resolved_semantic_grounding_service = (
        metadata_semantic_grounding_service
        or getattr(metadata_fetcher, "semantic_grounding_service", None)
        or MetadataSemanticGroundingService()
    )
    resolved_catalog_gateway = (
        catalog_gateway
        or getattr(metadata_fetcher, "catalog_gateway", None)
        or MetadataCatalogGateway(payload_mapper=resolved_payload_mapper)
    )
    resolved_ranking_service = (
        metadata_ranking_service
        or getattr(metadata_fetcher, "ranking_service", None)
        or (
            metadata_fetcher
            if metadata_fetcher is not None
            and hasattr(metadata_fetcher, "filter_tables")
            else None
        )
        or MetadataRankingService(
            payload_mapper=resolved_payload_mapper,
            semantic_grounding_service=resolved_semantic_grounding_service,
            usage_store=metadata_usage_store,
        )
    )
    resolved_table_loader = (
        metadata_table_loader
        or getattr(metadata_fetcher, "table_loader", None)
        or (
            metadata_fetcher
            if metadata_fetcher is not None and hasattr(metadata_fetcher, "fetch_tables")
            else None
        )
        or MetadataTableLoader(
            catalog_gateway=resolved_catalog_gateway,
            payload_mapper=resolved_payload_mapper,
        )
    )
    resolved_catalog_search_service = (
        metadata_catalog_search_service
        or getattr(metadata_fetcher, "catalog_search_service", None)
        or (
            metadata_fetcher
            if metadata_fetcher is not None and hasattr(metadata_fetcher, "search_catalog")
            else None
        )
        or MetadataCatalogSearchService(
            catalog_gateway=resolved_catalog_gateway,
            payload_mapper=resolved_payload_mapper,
            ranking_service=resolved_ranking_service,
            semantic_expansion_service=MetadataCatalogSemanticExpansionService(
                catalog_gateway=resolved_catalog_gateway,
                payload_mapper=resolved_payload_mapper,
                ranking_service=resolved_ranking_service,
            ),
        )
    )

    return MetadataRuntimeServices(
        payload_mapper=resolved_payload_mapper,
        catalog_gateway=resolved_catalog_gateway,
        table_loader=resolved_table_loader,
        ranking_service=resolved_ranking_service,
        catalog_search_service=resolved_catalog_search_service,
        semantic_grounding_service=resolved_semantic_grounding_service,
    )
