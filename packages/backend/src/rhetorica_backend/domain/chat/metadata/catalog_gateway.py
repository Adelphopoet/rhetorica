"""Transport gateway for MCP-backed metadata catalog access."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urljoin
from uuid import uuid4

import httpx
from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig
from rhetorica_backend.domain.chat.error_contract import retry_idempotent
from rhetorica_backend.domain.chat.metadata.catalog_query_policy import (
    MetadataCatalogQueryPolicy,
)
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper


class MetadataCatalogGateway:
    """Own MCP transport, query shaping, and entity retrieval."""

    REQUIRED_METADATA_FIELDS: tuple[str, ...] = (
        "columns",
        "database",
        "databaseSchema",
        "owners",
        "tags",
        "certification",
        "domains",
        "usageSummary",
    )

    def __init__(
        self,
        *,
        payload_mapper: MetadataPayloadMapper | None = None,
        query_policy: MetadataCatalogQueryPolicy | None = None,
    ) -> None:
        self._payload_mapper = payload_mapper or MetadataPayloadMapper()
        self._query_policy = query_policy or MetadataCatalogQueryPolicy()
        self.tool_call: Callable[..., dict[str, Any] | list[Any] | str] = (
            self.default_call_tool
        )

    @staticmethod
    def mcp_headers(api_token: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        return headers

    @staticmethod
    def bounded_int(
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return max(minimum, min(maximum, parsed))
        return default

    def merge_requested_fields(self, raw_fields: str) -> str:
        requested = [
            field_name.strip()
            for field_name in raw_fields.split(",")
            if isinstance(field_name, str) and field_name.strip()
        ]
        seen = {field_name.lower() for field_name in requested}
        merged = list(requested)
        for field_name in self.REQUIRED_METADATA_FIELDS:
            if field_name.lower() in seen:
                continue
            merged.append(field_name)
            seen.add(field_name.lower())
        return ",".join(merged)

    @staticmethod
    def normalize_entity_type(value: Any) -> str | None:
        return MetadataCatalogQueryPolicy.normalize_entity_type(value)

    @classmethod
    def _normalize_scope_value(cls, value: Any) -> str | None:
        return MetadataCatalogQueryPolicy._normalize_scope_value(value)

    @classmethod
    def _scope_filter_clauses(
        cls,
        *,
        service: str | None,
        database: str | None,
        schema: str | None,
    ) -> list[dict[str, Any]]:
        return MetadataCatalogQueryPolicy._scope_filter_clauses(
            service=service,
            database=database,
            schema=schema,
        )

    @staticmethod
    def _is_identifier_query(query: str | None) -> bool:
        return MetadataCatalogQueryPolicy.is_identifier_query(query)

    @classmethod
    def build_mcp_query_filter(
        cls,
        entity_type: str | None,
        query: str | None = None,
        *,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> str:
        return MetadataCatalogQueryPolicy.build_mcp_query_filter(
            entity_type=entity_type,
            query=query,
            service=service,
            database=database,
            schema=schema,
        )

    @classmethod
    def build_mcp_search_query(
        cls,
        entity_type: str | None,
        query: str | None,
    ) -> str:
        return MetadataCatalogQueryPolicy.build_mcp_search_query(
            entity_type=entity_type,
            query=query,
        )

    def default_call_tool(
        self,
        *,
        mcp_url: str,
        api_token: str | None,
        verify_tls: bool,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | list[Any] | str:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        def _request() -> dict[str, Any]:
            with httpx.Client(timeout=30.0, verify=verify_tls) as client:
                response = client.post(
                    mcp_url,
                    headers=self.mcp_headers(api_token),
                    json=payload,
                )
                response.raise_for_status()
                return response.json()

        body = retry_idempotent(_request, max_attempts=3)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            message = (
                self._payload_mapper.to_optional_str(body["error"].get("message"))
                or "mcp_tool_call_failed"
            )
            raise TypeError(message)

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            return {}
        content = result.get("content")
        if not isinstance(content, list):
            return result
        return self._parse_tool_text_payload(content)

    def _parse_tool_text_payload(
        self,
        content: list[dict[str, Any]],
    ) -> dict[str, Any] | list[Any] | str:
        parsed_chunks: list[Any] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw_text = self._payload_mapper.to_optional_str(item.get("text"))
            if raw_text is None:
                continue
            try:
                parsed_chunks.append(json.loads(raw_text))
            except json.JSONDecodeError:
                parsed_chunks.append(raw_text)

        if not parsed_chunks:
            return {}
        if len(parsed_chunks) == 1:
            return parsed_chunks[0]
        return parsed_chunks

    def search_raw_results(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        query: str,
        entity_type: str | None,
        limit: int,
        offset: int,
        service: str | None = None,
        database: str | None = None,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        if not connection.mcp_tables_url:
            return []

        collected: list[dict[str, Any]] = []
        fields = self.merge_requested_fields(connection.fields)
        current_offset = offset
        target_count = max(1, limit)

        while len(collected) < target_count:
            page_size = min(50, target_count - len(collected))
            search_request = self._query_policy.build_search_request(
                entity_type=entity_type,
                query=query,
                service=service,
                database=database,
                schema=schema,
            )
            payload = self.tool_call(
                mcp_url=connection.mcp_tables_url,
                api_token=connection.api_token,
                verify_tls=connection.verify_tls,
                tool_name="search_metadata",
                arguments={
                    "query": search_request.query_text,
                    "queryFilter": search_request.query_filter,
                    "fields": fields,
                    "size": page_size,
                    "from": current_offset,
                    **(
                        {"entityType": search_request.entity_type}
                        if search_request.entity_type
                        else {}
                    ),
                },
            )
            page_results = self._payload_mapper.extract_search_metadata_results(payload)
            if not page_results:
                break
            collected.extend(page_results)
            if len(page_results) < page_size:
                break
            current_offset += len(page_results)

        return collected

    def get_entity_details(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        if not connection.mcp_tables_url:
            warnings.append("metadata_mcp_unconfigured")
            return None, warnings

        normalized_entity_type = self.normalize_entity_type(entity_type)
        if normalized_entity_type is None or not fqn:
            return None, warnings

        if normalized_entity_type == "table":
            payload, direct_warnings = self.get_openmetadata_table_entity_details(
                connection=connection,
                fqn=fqn,
            )
            warnings.extend(direct_warnings)
            if payload is not None:
                normalized_payload = self._payload_mapper.normalize_table_entity_details(
                    payload,
                    normalize_entity_type=self.normalize_entity_type,
                )
                return {"entity": normalized_payload or payload}, warnings

        if not connection.mcp_tables_url:
            warnings.append("metadata_mcp_unconfigured")
            return None, warnings

        try:
            payload = self.tool_call(
                mcp_url=connection.mcp_tables_url,
                api_token=connection.api_token,
                verify_tls=connection.verify_tls,
                tool_name="get_entity_details",
                arguments={"entity_type": normalized_entity_type, "fqn": fqn},
            )
        except httpx.HTTPStatusError as exc:
            warnings.append(f"metadata_mcp_http_{exc.response.status_code}")
            return None, warnings
        except httpx.HTTPError:
            warnings.append("metadata_mcp_unreachable")
            return None, warnings
        except Exception:
            warnings.append("metadata_mcp_failed")
            return None, warnings

        normalized_payload = self._payload_mapper.normalize_table_entity_details(
            payload,
            normalize_entity_type=self.normalize_entity_type,
        )
        return {"entity": normalized_payload or payload}, warnings

    def get_openmetadata_table_entity_details(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        fqn: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        clean_fqn = fqn.strip()
        if not clean_fqn:
            return None, warnings
        base_url = getattr(connection, "base_url", None)
        if not isinstance(base_url, str) or not base_url.strip():
            return None, warnings

        endpoint = urljoin(
            base_url.rstrip("/") + "/",
            f"api/v1/tables/name/{quote(clean_fqn, safe='')}",
        )
        headers: dict[str, str] = {}
        api_token = getattr(connection, "api_token", None)
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        raw_fields = getattr(connection, "fields", "columns,database,databaseSchema")
        params = {"fields": self.merge_requested_fields(raw_fields)}
        verify_tls = bool(getattr(connection, "verify_tls", True))

        try:
            with httpx.Client(timeout=25.0, verify=verify_tls) as client:
                response = client.get(endpoint, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            warnings.append(f"metadata_openmetadata_http_{exc.response.status_code}")
            return None, warnings
        except httpx.HTTPError:
            warnings.append("metadata_openmetadata_unreachable")
            return None, warnings
        except Exception:
            warnings.append("metadata_openmetadata_failed")
            return None, warnings

        return payload if isinstance(payload, dict) else None, warnings

    def get_entity_lineage(
        self,
        *,
        connection: OpenMetadataConnectionConfig,
        entity_type: str,
        fqn: str,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        if not connection.mcp_tables_url:
            warnings.append("metadata_mcp_unconfigured")
            return None, warnings

        normalized_entity_type = self.normalize_entity_type(entity_type)
        if normalized_entity_type is None or not fqn:
            return None, warnings

        safe_upstream_depth = self.bounded_int(
            upstream_depth,
            default=2,
            minimum=0,
            maximum=10,
        )
        safe_downstream_depth = self.bounded_int(
            downstream_depth,
            default=2,
            minimum=0,
            maximum=10,
        )
        try:
            payload = self.tool_call(
                mcp_url=connection.mcp_tables_url,
                api_token=connection.api_token,
                verify_tls=connection.verify_tls,
                tool_name="get_entity_lineage",
                arguments={
                    "entity_type": normalized_entity_type,
                    "fqn": fqn,
                    "upstream_depth": safe_upstream_depth,
                    "downstream_depth": safe_downstream_depth,
                },
            )
        except httpx.HTTPStatusError as exc:
            warnings.append(f"metadata_mcp_http_{exc.response.status_code}")
            return None, warnings
        except httpx.HTTPError:
            warnings.append("metadata_mcp_unreachable")
            return None, warnings
        except Exception:
            warnings.append("metadata_mcp_failed")
            return None, warnings
        return {"lineage": payload}, warnings
