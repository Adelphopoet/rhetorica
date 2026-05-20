"""Metadata table loading from raw sources."""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any, ClassVar

import httpx
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.metadata.catalog_gateway import MetadataCatalogGateway
from rhetorica_backend.domain.chat.metadata.models import MetadataCacheEntry, TableContext
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper


class MetadataTableLoader:
    """Load raw metadata tables from OpenMetadata and MCP, with cache and dedupe."""

    _cache: ClassVar[dict[str, MetadataCacheEntry]] = {}

    def __init__(
        self,
        *,
        catalog_gateway: MetadataCatalogGateway | None = None,
        payload_mapper: MetadataPayloadMapper | None = None,
    ) -> None:
        self._catalog_gateway = catalog_gateway or MetadataCatalogGateway()
        self._payload_mapper = payload_mapper or MetadataPayloadMapper()
        self.openmetadata_loader: Callable[[OpenMetadataConnectionConfig], list[TableContext]] = (
            self.default_fetch_openmetadata_tables
        )
        self.mcp_loader: Callable[[OpenMetadataConnectionConfig], list[TableContext]] = (
            self.default_fetch_mcp_tables
        )

    def fetch_tables(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        workspace_id: str | None = None,
        connector_name: str | None = None,
    ) -> tuple[list[TableContext], list[str]]:
        cache_key = self._metadata_cache_key(
            workspace_id=workspace_id,
            connector_name=connector_name,
            connection=connection,
        )
        now = time.time()
        cache_entry = self._cache.get(cache_key)
        if cache_entry:
            if now < cache_entry.expires_at:
                return deepcopy(cache_entry.tables), list(cache_entry.warnings)
            if now < cache_entry.stale_until:
                try:
                    refreshed_tables, refreshed_warnings = self._fetch_tables_uncached(
                        connection=connection
                    )
                    self._cache[cache_key] = self._new_cache_entry(
                        connection=connection,
                        tables=refreshed_tables,
                        warnings=refreshed_warnings,
                        now=now,
                    )
                    return deepcopy(refreshed_tables), list(refreshed_warnings)
                except Exception:
                    stale_warnings = list(cache_entry.warnings)
                    if "metadata_cache_stale" not in stale_warnings:
                        stale_warnings.append("metadata_cache_stale")
                    return deepcopy(cache_entry.tables), stale_warnings

        tables, warnings = self._fetch_tables_uncached(connection=connection)
        self._cache[cache_key] = self._new_cache_entry(
            connection=connection,
            tables=tables,
            warnings=warnings,
            now=now,
        )
        return deepcopy(tables), list(warnings)

    def _fetch_tables_uncached(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
    ) -> tuple[list[TableContext], list[str]]:
        results: list[TableContext] = []
        warnings: list[str] = []

        try:
            results.extend(self.openmetadata_loader(connection))
        except httpx.HTTPStatusError as exc:
            warnings.append(f"metadata_openmetadata_http_{exc.response.status_code}")
        except httpx.HTTPError:
            warnings.append("metadata_openmetadata_unreachable")
        except Exception:
            warnings.append("metadata_openmetadata_failed")

        if connection.mcp_tables_url:
            try:
                results.extend(self.mcp_loader(connection))
            except httpx.HTTPStatusError as exc:
                warnings.append(f"metadata_mcp_http_{exc.response.status_code}")
            except httpx.HTTPError:
                warnings.append("metadata_mcp_unreachable")
            except Exception:
                warnings.append("metadata_mcp_failed")

        unique: dict[str, TableContext] = {}
        for table in results:
            existing = unique.get(table.fqn)
            if existing is None:
                unique[table.fqn] = table
                continue
            unique[table.fqn] = self._merge_table_contexts(existing, table)
        return list(unique.values()), warnings

    def _new_cache_entry(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        tables: list[TableContext],
        warnings: list[str],
        now: float,
    ) -> MetadataCacheEntry:
        ttl = self._cache_ttl_seconds(connection)
        stale_window = self._cache_stale_window_seconds(connection)
        return MetadataCacheEntry(
            tables=deepcopy(tables),
            warnings=list(warnings),
            expires_at=now + ttl,
            stale_until=now + ttl + stale_window,
        )

    @staticmethod
    def _merge_table_contexts(existing: TableContext, candidate: TableContext) -> TableContext:
        merged_columns = list(existing.columns)
        for column in candidate.columns:
            if column not in merged_columns:
                merged_columns.append(column)

        merged_column_types = dict(existing.column_types)
        merged_column_types.update(candidate.column_types)
        merged_column_descriptions = dict(existing.column_descriptions)
        merged_column_descriptions.update(candidate.column_descriptions)

        return TableContext(
            source=existing.source if existing.source == "mcp" else candidate.source,
            fqn=existing.fqn,
            columns=merged_columns,
            column_types=merged_column_types,
            column_descriptions=merged_column_descriptions,
            description=candidate.description or existing.description,
            service=candidate.service or existing.service,
            database=candidate.database or existing.database,
            schema=candidate.schema or existing.schema,
            owners=list(dict.fromkeys([*existing.owners, *candidate.owners])),
            owner_refs=list(dict.fromkeys([*existing.owner_refs, *candidate.owner_refs])),
            owner_teams=list(dict.fromkeys([*existing.owner_teams, *candidate.owner_teams])),
            tags=list(dict.fromkeys([*existing.tags, *candidate.tags])),
            domains=list(dict.fromkeys([*existing.domains, *candidate.domains])),
            domain_refs=list(dict.fromkeys([*existing.domain_refs, *candidate.domain_refs])),
            glossary_terms=list(
                dict.fromkeys([*existing.glossary_terms, *candidate.glossary_terms])
            ),
            related_metrics=list(
                dict.fromkeys([*existing.related_metrics, *candidate.related_metrics])
            ),
            quality_summary=candidate.quality_summary or existing.quality_summary,
            tier=candidate.tier or existing.tier,
            certification=candidate.certification or existing.certification,
            usage_count=max(existing.usage_count, candidate.usage_count),
            relevance_score=max(existing.relevance_score, candidate.relevance_score),
            matched_on=list(dict.fromkeys([*existing.matched_on, *candidate.matched_on])),
            usage_frequency=max(existing.usage_frequency, candidate.usage_frequency),
        )

    @staticmethod
    def _cache_ttl_seconds(connection: OpenMetadataConnectionConfig) -> int:
        raw = connection.extra_settings.get("metadata_cache_ttl_seconds")
        if isinstance(raw, int | float):
            value = int(raw)
            if 10 <= value <= 3_600:
                return value
        return 120

    @staticmethod
    def _cache_stale_window_seconds(connection: OpenMetadataConnectionConfig) -> int:
        raw = connection.extra_settings.get("metadata_cache_stale_window_seconds")
        if isinstance(raw, int | float):
            value = int(raw)
            if 0 <= value <= 21_600:
                return value
        return 600

    @staticmethod
    def _metadata_cache_key(
        *,
        workspace_id: str | None,
        connector_name: str | None,
        connection: OpenMetadataConnectionConfig,
    ) -> str:
        workspace = workspace_id or "default"
        connector = connector_name or "default"
        shape = "|".join(
            [
                connection.base_url,
                str(connection.verify_tls),
                str(connection.mcp_tables_url or ""),
                str(connection.service_name or ""),
                str(connection.database_name or ""),
                str(connection.schema_name or ""),
                str(connection.table_limit),
                connection.fields,
            ]
        )
        return f"{workspace}:{connector}:{shape}"

    def default_fetch_openmetadata_tables(
        self,
        connection: OpenMetadataConnectionConfig,
    ) -> list[TableContext]:
        from urllib.parse import urljoin

        endpoint = urljoin(connection.base_url.rstrip("/") + "/", "api/v1/tables")
        headers: dict[str, str] = {}
        if connection.api_token:
            headers["Authorization"] = f"Bearer {connection.api_token}"

        page_size = max(1, min(connection.table_limit, 200))
        params: dict[str, Any] = {
            "limit": page_size,
            "fields": self._catalog_gateway.merge_requested_fields(connection.fields),
        }
        if connection.database_name:
            params["database"] = connection.database_name
        if connection.schema_name:
            params["databaseSchema"] = connection.schema_name

        tables: list[TableContext] = []
        after: str | None = None
        while len(tables) < connection.table_limit:
            page_params = dict(params)
            if after:
                page_params["after"] = after

            def _request(page_params: dict[str, Any] = page_params) -> dict[str, Any]:
                with httpx.Client(timeout=25.0, verify=connection.verify_tls) as client:
                    response = client.get(endpoint, headers=headers, params=page_params)
                    response.raise_for_status()
                    return response.json()

            body = retry_idempotent(_request, max_attempts=3)
            data = body.get("data", [])
            if not isinstance(data, list) or not data:
                break

            for item in data:
                fqn = item.get("fullyQualifiedName") or item.get("name")
                if not fqn:
                    continue
                columns: list[str] = []
                column_types: dict[str, str] = {}
                column_descriptions: dict[str, str] = {}
                for col in item.get("columns", []):
                    if not isinstance(col, dict):
                        continue
                    name = col.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    clean_name = name.strip()
                    columns.append(clean_name)
                    dtype = col.get("dataTypeDisplay") or col.get("dataType")
                    if isinstance(dtype, str) and dtype.strip():
                        column_types[clean_name] = dtype.strip()
                    description = col.get("description")
                    if isinstance(description, str) and description.strip():
                        column_descriptions[clean_name] = description.strip()

                tags = self._payload_mapper.extract_tag_fqns(item.get("tags"))
                owner_refs = self._payload_mapper.extract_entity_refs(item.get("owners"))
                domain_refs = self._payload_mapper.extract_entity_refs(
                    item.get("domains") or item.get("domain")
                )
                tables.append(
                    TableContext(
                        source="openmetadata",
                        fqn=str(fqn),
                        columns=columns,
                        column_types=column_types,
                        column_descriptions=column_descriptions,
                        description=self._payload_mapper.to_optional_str(item.get("description")),
                        service=self._payload_mapper.extract_nested_fqn(item, "database", 0),
                        database=self._payload_mapper.extract_nested_fqn(item, "database", 1),
                        schema=self._payload_mapper.extract_nested_fqn(item, "databaseSchema", -1),
                        owners=self._payload_mapper.extract_entity_names(item.get("owners")),
                        owner_refs=owner_refs,
                        owner_teams=[
                            ref.name
                            for ref in owner_refs
                            if (ref.type or "").strip().lower() == "team"
                        ],
                        tags=tags,
                        domains=self._payload_mapper.extract_entity_names(
                            item.get("domains") or item.get("domain")
                        ),
                        domain_refs=domain_refs,
                        glossary_terms=self._payload_mapper.extract_glossary_terms(item),
                        related_metrics=self._payload_mapper.extract_metric_refs(
                            item.get("relatedMetrics") or item.get("metrics")
                        ),
                        tier=self._payload_mapper.extract_tier(tags),
                        certification=self._payload_mapper.extract_certification(tags),
                        usage_count=self._payload_mapper.extract_usage_count(
                            item.get("usageSummary")
                        ),
                    )
                )
                if len(tables) >= connection.table_limit:
                    break

            paging = body.get("paging")
            next_after = paging.get("after") if isinstance(paging, dict) else None
            if not isinstance(next_after, str) or not next_after.strip():
                break
            after = next_after.strip()
        return tables

    def default_fetch_mcp_tables(
        self,
        connection: OpenMetadataConnectionConfig,
    ) -> list[TableContext]:
        mcp_url = connection.mcp_tables_url
        if not mcp_url:
            return []

        legacy_error: Exception | None = None
        raw_tables: list[dict[str, Any]] = []
        try:
            def _request_legacy() -> Any:
                with httpx.Client(timeout=20.0, verify=connection.verify_tls) as client:
                    response = client.get(
                        mcp_url,
                        headers=self._catalog_gateway.mcp_headers(connection.api_token),
                    )
                    response.raise_for_status()
                    return response.json()

            raw_tables = self._parse_legacy_mcp_tables(
                retry_idempotent(_request_legacy, max_attempts=3)
            )
        except Exception as exc:
            legacy_error = exc

        if raw_tables:
            return self._table_contexts_from_mcp_rows(raw_tables)
        if not self._looks_like_mcp_endpoint(mcp_url):
            if legacy_error:
                raise legacy_error
            return []

        try:
            tool_tables = self._fetch_mcp_tables_via_tool_search(connection)
            if tool_tables:
                return tool_tables
        except Exception:
            if legacy_error:
                raise legacy_error from None
            raise

        if legacy_error:
            raise legacy_error
        return []

    @staticmethod
    def _looks_like_mcp_endpoint(url: str) -> bool:
        return "/mcp" in url.lower()

    @staticmethod
    def _parse_legacy_mcp_tables(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if isinstance(body, dict) and isinstance(body.get("tables"), list):
            return [row for row in body["tables"] if isinstance(row, dict)]
        return []

    def _fetch_mcp_tables_via_tool_search(
        self,
        connection: OpenMetadataConnectionConfig,
    ) -> list[TableContext]:
        target_count = max(1, min(connection.table_limit, 500))
        rows = self._catalog_gateway.search_raw_results(
            connection=connection,
            query="tables",
            entity_type="table",
            limit=target_count,
            offset=0,
            service=connection.service_name,
            database=connection.database_name,
            schema=connection.schema_name,
        )
        return self._table_contexts_from_mcp_rows(rows[:target_count])

    def _table_contexts_from_mcp_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[TableContext]:
        tables: list[TableContext] = []
        for item in rows:
            fqn = item.get("fqn") or item.get("fullyQualifiedName") or item.get("name")
            if not isinstance(fqn, str) or not fqn.strip():
                continue

            raw_columns = item.get("columns")
            columns: list[str] = []
            column_types: dict[str, str] = {}
            column_descriptions: dict[str, str] = {}
            if isinstance(raw_columns, list):
                for col in raw_columns:
                    if isinstance(col, dict):
                        name = col.get("name")
                        if isinstance(name, str) and name.strip():
                            clean_name = name.strip()
                            columns.append(clean_name)
                            dtype = col.get("dataType") or col.get("type")
                            if isinstance(dtype, str) and dtype.strip():
                                column_types[clean_name] = dtype.strip()
                            description = col.get("description")
                            if isinstance(description, str) and description.strip():
                                column_descriptions[clean_name] = description.strip()
                    else:
                        columns.append(str(col))
            elif isinstance(item.get("columnNames"), list):
                columns = [str(col) for col in item["columnNames"]]

            tags = self._payload_mapper.extract_tag_fqns(item.get("tags"))
            owner_refs = self._payload_mapper.extract_entity_refs(item.get("owners"))
            domain_refs = self._payload_mapper.extract_entity_refs(
                item.get("domains") or item.get("domain")
            )
            tables.append(
                TableContext(
                    source="mcp",
                    fqn=fqn.strip(),
                    columns=columns,
                    column_types=column_types,
                    column_descriptions=column_descriptions,
                    description=self._payload_mapper.to_optional_str(item.get("description")),
                    service=self._payload_mapper.to_optional_str(item.get("service"))
                    or self._payload_mapper.extract_nested_name(item.get("service")),
                    database=self._payload_mapper.to_optional_str(item.get("database"))
                    or self._payload_mapper.extract_nested_name(item.get("database")),
                    schema=self._payload_mapper.to_optional_str(item.get("schema"))
                    or self._payload_mapper.extract_nested_name(item.get("databaseSchema")),
                    owners=self._payload_mapper.extract_entity_names(item.get("owners")),
                    owner_refs=owner_refs,
                    owner_teams=[
                        ref.name
                        for ref in owner_refs
                        if (ref.type or "").strip().lower() == "team"
                    ],
                    tags=tags,
                    domains=self._payload_mapper.extract_entity_names(
                        item.get("domains") or item.get("domain")
                    ),
                    domain_refs=domain_refs,
                    glossary_terms=self._payload_mapper.extract_glossary_terms(item),
                    related_metrics=self._payload_mapper.extract_metric_refs(
                        item.get("relatedMetrics") or item.get("metrics")
                    ),
                    quality_summary=self._payload_mapper.extract_quality_summary(
                        item.get("quality_summary")
                    ),
                    tier=self._payload_mapper.extract_tier(tags),
                    certification=self._payload_mapper.extract_certification(tags),
                    usage_count=self._payload_mapper.extract_usage_count(
                        item.get("usageSummary")
                    ),
                )
            )
        return tables
