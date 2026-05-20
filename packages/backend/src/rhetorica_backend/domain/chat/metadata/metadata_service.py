"""Backward-compatible metadata facade built from smaller collaborators."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.catalog_search_service import (
    MetadataCatalogSearchService,
)
from rhetorica_backend.domain.chat.metadata.connection_resolver import (
    MetadataResolver,
    clone_metadata_connection_with_scope,
)
from rhetorica_backend.domain.chat.metadata.models import TableContext
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService
from rhetorica_backend.domain.chat.metadata.runtime_dependencies import (
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.metadata.semantic_grounding_service import (
    MetadataSemanticGroundingService,
)
from rhetorica_backend.domain.chat.metadata.table_loader import MetadataTableLoader

__all__ = [
    "MetadataFetcher",
    "MetadataResolver",
    "TableContext",
    "clone_metadata_connection_with_scope",
]


class MetadataFetcher:
    """Compatibility facade for legacy call sites while runtime uses smaller services."""

    def __init__(
        self,
        *,
        payload_mapper: MetadataPayloadMapper | None = None,
        catalog_gateway: MetadataCatalogGateway | None = None,
        table_loader: MetadataTableLoader | None = None,
        ranking_service: MetadataRankingService | None = None,
        semantic_grounding_service: MetadataSemanticGroundingService | None = None,
        catalog_search_service: MetadataCatalogSearchService | None = None,
    ) -> None:
        services = resolve_metadata_runtime_services(
            payload_mapper=payload_mapper,
            catalog_gateway=catalog_gateway,
            metadata_table_loader=table_loader,
            metadata_catalog_search_service=catalog_search_service,
            metadata_ranking_service=ranking_service,
            metadata_semantic_grounding_service=semantic_grounding_service,
        )
        self._payload_mapper = services.payload_mapper
        self._catalog_gateway = services.catalog_gateway
        self._table_loader = services.table_loader
        self._ranking_service = services.ranking_service
        self._catalog_search_service = services.catalog_search_service
        self._semantic_grounding_service = services.semantic_grounding_service

    @property
    def payload_mapper(self) -> MetadataPayloadMapper:
        return self._payload_mapper

    @property
    def catalog_gateway(self) -> MetadataCatalogGateway:
        return self._catalog_gateway

    @property
    def table_loader(self) -> MetadataTableLoader:
        return self._table_loader

    @property
    def ranking_service(self) -> MetadataRankingService:
        return self._ranking_service

    @property
    def semantic_grounding_service(self) -> MetadataSemanticGroundingService:
        return self._semantic_grounding_service

    @property
    def catalog_search_service(self) -> MetadataCatalogSearchService:
        return self._catalog_search_service

    def _mcp_call_tool(
        self,
        mcp_url: str,
        api_token: str | None,
        verify_tls: bool,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | list[Any] | str:
        return self._catalog_gateway.default_call_tool(
            mcp_url=mcp_url,
            api_token=api_token,
            verify_tls=verify_tls,
            tool_name=tool_name,
            arguments=arguments,
        )

    def _fetch_openmetadata_tables(
        self,
        connection: OpenMetadataConnectionConfig,
    ) -> list[TableContext]:
        return self._table_loader.default_fetch_openmetadata_tables(connection)

    def _fetch_mcp_tables(
        self,
        connection: OpenMetadataConnectionConfig,
    ) -> list[TableContext]:
        return self._table_loader.default_fetch_mcp_tables(connection)

    def fetch_tables(
        self,
        connection: OpenMetadataConnectionConfig,
        workspace_id: str | None = None,
        connector_name: str | None = None,
    ) -> tuple[list[TableContext], list[str]]:
        self._table_loader.openmetadata_loader = self._fetch_openmetadata_tables
        self._table_loader.mcp_loader = self._fetch_mcp_tables
        return self._table_loader.fetch_tables(
            connection=connection,
            workspace_id=workspace_id,
            connector_name=connector_name,
        )

    def filter_tables(
        self,
        tables: list[TableContext],
        search: str | None,
        service: str | None,
        database: str | None,
        schema: str | None,
        *,
        workspace_id: str | None = None,
        connector_name: str | None = None,
        top_n: int = 20,
        preferred_service: str | None = None,
        preferred_database: str | None = None,
        preferred_schema: str | None = None,
    ) -> list[TableContext]:
        return self._ranking_service.filter_tables(
            tables=tables,
            search=search,
            service=service,
            database=database,
            schema=schema,
            workspace_id=workspace_id,
            connector_name=connector_name,
            top_n=top_n,
            preferred_service=preferred_service,
            preferred_database=preferred_database,
            preferred_schema=preferred_schema,
        )

    def search_catalog(
        self,
        connection: OpenMetadataConnectionConfig,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        *,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        self._catalog_gateway.tool_call = self._mcp_call_tool
        return self._catalog_search_service.search_catalog(
            connection=connection,
            query=query,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
            service=service,
            database=database,
            schema=schema,
        )

    def search_catalog_with_ranked_results(
        self,
        connection: OpenMetadataConnectionConfig,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        *,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        self._catalog_gateway.tool_call = self._mcp_call_tool
        return self._catalog_search_service.search_catalog_with_ranked_results(
            connection=connection,
            query=query,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
            service=service,
            database=database,
            schema=schema,
        )

    def get_entity_details(
        self,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        self._catalog_gateway.tool_call = self._mcp_call_tool
        return self._catalog_search_service.get_entity_details(
            connection=connection,
            entity_type=entity_type,
            fqn=fqn,
        )

    def get_entity_lineage(
        self,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        self._catalog_gateway.tool_call = self._mcp_call_tool
        return self._catalog_search_service.get_entity_lineage(
            connection=connection,
            entity_type=entity_type,
            fqn=fqn,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
        )

    @staticmethod
    def _bounded_int(
        value: Any,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        return MetadataCatalogGateway.bounded_int(
            value,
            default=default,
            minimum=minimum,
            maximum=maximum,
        )

    @classmethod
    def _build_mcp_query_filter(
        cls,
        entity_type: str | None,
        query: str | None = None,
        *,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        return MetadataCatalogGateway.build_mcp_query_filter(
            entity_type=entity_type,
            query=query,
            service=service,
            database=database,
            schema=schema,
        )

    @staticmethod
    def _table_context_from_any_payload(value: Any) -> TableContext | None:
        mapper = MetadataPayloadMapper()
        return mapper.table_context_from_any_payload(
            value,
            normalize_entity_type=MetadataCatalogGateway.normalize_entity_type,
        )

    @classmethod
    def is_suspicious_table(cls, table: TableContext) -> bool:
        return MetadataSemanticGroundingService.is_suspicious_table(table)

    @classmethod
    def semantic_reason_codes(cls, table: TableContext) -> list[str]:
        return MetadataSemanticGroundingService.semantic_reason_codes(table)

    @classmethod
    def semantic_candidate_payload(cls, table: TableContext) -> dict[str, Any]:
        return MetadataSemanticGroundingService.semantic_candidate_payload(table)

    @classmethod
    def build_semantic_grounding_payload(
        cls,
        tables: list[TableContext],
        *,
        chosen_table_fqn: str | None = None,
        lineage_summary: str | None = None,
    ) -> dict[str, Any] | None:
        return MetadataSemanticGroundingService.build_semantic_grounding_payload(
            tables,
            chosen_table_fqn=chosen_table_fqn,
            lineage_summary=lineage_summary,
        )
