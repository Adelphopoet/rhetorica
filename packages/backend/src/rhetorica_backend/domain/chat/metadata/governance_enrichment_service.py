"""OpenMetadata governance enrichment for chat metadata candidates."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urljoin

import httpx
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.metadata.models import DataQualitySummary, TableContext
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper


@dataclass(frozen=True)
class _QualityCacheEntry:
    value: DataQualitySummary | None
    expires_at: float


class OpenMetadataGovernanceEnrichmentService:
    """Fetch bounded read-only governance signals for selected OM entities."""

    _quality_cache: ClassVar[dict[str, _QualityCacheEntry]] = {}
    _DEFAULT_LIMIT = 5
    _DEFAULT_TTL_SECONDS = 300

    def __init__(self, *, payload_mapper: MetadataPayloadMapper | None = None) -> None:
        self._payload_mapper = payload_mapper or MetadataPayloadMapper()

    def enrich_tables(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        tables: list[TableContext],
    ) -> tuple[list[TableContext], list[str]]:
        if not tables:
            return [], []

        limit = self.enrichment_limit(connection)
        enriched: list[TableContext] = []
        warnings: list[str] = []
        for index, table in enumerate(tables):
            if index >= limit:
                enriched.append(table)
                continue
            quality, quality_warnings = self._quality_summary_for_table(
                connection=connection,
                table_fqn=table.fqn,
            )
            warnings.extend(quality_warnings)
            enriched.append(self._with_quality(table=table, quality_summary=quality))
        warnings.extend(self.quality_warnings(enriched[:limit]))
        return enriched, self._unique(warnings)

    def enrich_catalog_rows(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        limit = self.enrichment_limit(connection)
        enriched: list[dict[str, Any]] = []
        warnings: list[str] = []
        enriched_count = 0
        for row in rows:
            if str(row.get("entityType") or "").strip().lower() != "table":
                enriched.append(row)
                continue
            if enriched_count >= limit:
                enriched.append(row)
                continue
            fqn = self._payload_mapper.to_optional_str(row.get("fullyQualifiedName"))
            if not fqn:
                enriched.append(row)
                continue
            quality, quality_warnings = self._quality_summary_for_table(
                connection=connection,
                table_fqn=fqn,
            )
            warnings.extend(quality_warnings)
            candidate = dict(row)
            if quality is not None:
                candidate["quality_summary"] = quality.to_payload()
                candidate["quality_status"] = quality.status
            enriched.append(candidate)
            enriched_count += 1
        warnings.extend(self.quality_row_warnings(enriched[:limit]))
        return enriched, self._unique(warnings)

    def enrich_entity_details(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        entity: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        if entity is None:
            return None, []
        payload = dict(entity)
        entity_body = payload.get("entity") if isinstance(payload.get("entity"), dict) else payload
        if not isinstance(entity_body, dict):
            return payload, []
        if str(entity_body.get("entityType") or "").strip().lower() != "table":
            return payload, []
        fqn = self._payload_mapper.to_optional_str(entity_body.get("fullyQualifiedName"))
        if not fqn:
            return payload, []
        quality, warnings = self._quality_summary_for_table(
            connection=connection,
            table_fqn=fqn,
        )
        if quality is not None:
            entity_body["quality_summary"] = quality.to_payload()
            entity_body["quality_status"] = quality.status
        return payload, self._unique([*warnings, *self.quality_row_warnings([entity_body])])

    def enrichment_limit(self, connection: OpenMetadataConnectionConfig) -> int:
        extra_settings = getattr(connection, "extra_settings", {})
        raw = extra_settings.get("governance_enrichment_limit") if isinstance(extra_settings, dict) else None
        if isinstance(raw, bool):
            return self._DEFAULT_LIMIT
        if isinstance(raw, int | float):
            return max(0, min(25, int(raw)))
        return self._DEFAULT_LIMIT

    def quality_warnings(self, tables: list[TableContext]) -> list[str]:
        warnings: list[str] = []
        for table in tables:
            quality = table.quality_summary
            if quality is None:
                continue
            if quality.failed > 0:
                warnings.append("metadata_quality_failed")
            if quality.aborted > 0:
                warnings.append("metadata_quality_aborted")
        return self._unique(warnings)

    def quality_row_warnings(self, rows: list[dict[str, Any]]) -> list[str]:
        warnings: list[str] = []
        for row in rows:
            quality = row.get("quality_summary")
            if not isinstance(quality, dict):
                continue
            if int(quality.get("failed") or 0) > 0:
                warnings.append("metadata_quality_failed")
            if int(quality.get("aborted") or 0) > 0:
                warnings.append("metadata_quality_aborted")
        return self._unique(warnings)

    def _quality_summary_for_table(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        table_fqn: str,
    ) -> tuple[DataQualitySummary | None, list[str]]:
        if self.enrichment_limit(connection) <= 0:
            return None, []
        if not isinstance(getattr(connection, "base_url", None), str):
            return None, []

        cache_key = self._quality_cache_key(connection=connection, table_fqn=table_fqn)
        now = time.time()
        cached = self._quality_cache.get(cache_key)
        if cached is not None and now < cached.expires_at:
            return cached.value, []

        try:
            body = self._fetch_quality_payload(connection=connection, table_fqn=table_fqn)
        except httpx.HTTPStatusError as exc:
            return None, [f"metadata_quality_http_{exc.response.status_code}"]
        except httpx.HTTPError:
            return None, ["metadata_quality_unreachable"]
        except Exception:
            return None, ["metadata_quality_failed_fetch"]

        summary = self._payload_mapper.extract_quality_summary(body)
        self._quality_cache[cache_key] = _QualityCacheEntry(
            value=summary,
            expires_at=now + self._cache_ttl_seconds(connection),
        )
        return summary, []

    def _fetch_quality_payload(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        table_fqn: str,
    ) -> dict[str, Any]:
        endpoint = urljoin(connection.base_url.rstrip("/") + "/", "api/v1/dataQuality/testCases")
        headers: dict[str, str] = {}
        if connection.api_token:
            headers["Authorization"] = f"Bearer {connection.api_token}"
        params: dict[str, Any] = {
            "limit": 100,
            "fields": "testDefinition,testCaseResult,owners",
            "includeAllTests": "true",
            "orderByLastExecutionDate": "true",
            "entityLink": f"<#E::table::{table_fqn}>",
        }

        def _request() -> dict[str, Any]:
            with httpx.Client(timeout=20.0, verify=connection.verify_tls) as client:
                response = client.get(endpoint, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        return retry_idempotent(_request, max_attempts=3)

    def _with_quality(
        self,
        *,
        table: TableContext,
        quality_summary: DataQualitySummary | None,
    ) -> TableContext:
        if quality_summary is None:
            return table
        return TableContext(
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
            quality_summary=quality_summary,
            tier=table.tier,
            certification=table.certification,
            usage_count=table.usage_count,
            relevance_score=table.relevance_score,
            matched_on=list(table.matched_on),
            usage_frequency=table.usage_frequency,
        )

    def _cache_ttl_seconds(self, connection: OpenMetadataConnectionConfig) -> int:
        extra_settings = getattr(connection, "extra_settings", {})
        raw = extra_settings.get("governance_enrichment_ttl_seconds") if isinstance(extra_settings, dict) else None
        if isinstance(raw, bool):
            return self._DEFAULT_TTL_SECONDS
        if isinstance(raw, int | float):
            return max(30, min(3_600, int(raw)))
        return self._DEFAULT_TTL_SECONDS

    @staticmethod
    def _quality_cache_key(
        *,
        connection: OpenMetadataConnectionConfig,
        table_fqn: str,
    ) -> str:
        return "|".join(
            [
                connection.base_url,
                str(connection.verify_tls),
                table_fqn.strip().lower(),
            ]
        )

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))
