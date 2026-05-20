"""Thin metadata catalog facade for MCP and other non-orchestration integrations."""

from __future__ import annotations

from typing import Any

from rhetorica_backend.domain.chat.metadata.metadata_service import MetadataFetcher
from rhetorica_backend.domain.chat.metadata.models import TableContext
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService
from rhetorica_backend.domain.chat.metadata.runtime_dependencies import (
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.metadata.table_loader import MetadataTableLoader


class MetadataCatalogService:
    """Read-only metadata access without dragging legacy chat services into runtime."""

    def __init__(
        self,
        *,
        openmetadata_connector_repository: Any,
        metadata_fetcher: MetadataFetcher | None = None,
        metadata_table_loader: MetadataTableLoader | None = None,
        metadata_ranking_service: MetadataRankingService | None = None,
    ) -> None:
        self._openmetadata_connector_repository = openmetadata_connector_repository
        metadata_services = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_table_loader=metadata_table_loader,
            metadata_ranking_service=metadata_ranking_service,
        )
        self._metadata_table_loader = metadata_services.table_loader
        self._metadata_ranking_service = metadata_services.ranking_service

    def resolve_connection(
        self,
        *,
        workspace_id: str,
        metadata_connector_name: str | None = None,
    ) -> Any | None:
        if metadata_connector_name:
            return self._openmetadata_connector_repository.get_connection(
                workspace_id=workspace_id,
                name=metadata_connector_name,
            )
        connectors = self._openmetadata_connector_repository.list(
            workspace_id=workspace_id,
            limit=100,
        )
        connectors = [connector for connector in connectors if connector.is_active]
        default_connector_name = connectors[0].name if connectors else None
        if default_connector_name is None:
            return None
        return self._openmetadata_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=default_connector_name,
        )

    def list_tables(
        self,
        *,
        workspace_id: str,
        metadata_connector_name: str | None = None,
    ) -> tuple[list[TableContext], list[str]]:
        connection = self.resolve_connection(
            workspace_id=workspace_id,
            metadata_connector_name=metadata_connector_name,
        )
        if connection is None:
            return [], ["metadata_connector_missing"]
        return self._metadata_table_loader.fetch_tables(
            connection=connection,
            workspace_id=workspace_id,
            connector_name=metadata_connector_name,
        )

    def search_tables(
        self,
        *,
        search_query: str,
        workspace_id: str,
        metadata_connector_name: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = self.resolve_connection(
            workspace_id=workspace_id,
            metadata_connector_name=metadata_connector_name,
        )
        if connection is None:
            return []
        tables, _warnings = self._metadata_table_loader.fetch_tables(
            connection=connection,
            workspace_id=workspace_id,
            connector_name=metadata_connector_name,
        )
        ranked = self._metadata_ranking_service.filter_tables(
            tables=tables,
            search=search_query,
            service=None,
            database=None,
            schema=None,
            workspace_id=workspace_id,
            connector_name=metadata_connector_name,
            top_n=20,
            preferred_service=getattr(connection, "service_name", None),
            preferred_database=getattr(connection, "database_name", None),
            preferred_schema=getattr(connection, "schema_name", None),
        )
        return [
            {
                "fqn": ctx.fqn,
                "source": ctx.source,
                "columns": list(ctx.columns),
                "service": ctx.service,
                "database": ctx.database,
                "schema": ctx.schema,
                "description": ctx.description,
                "relevance_score": round(ctx.relevance_score, 3),
                "matched_on": list(ctx.matched_on),
            }
            for ctx in ranked
        ]
