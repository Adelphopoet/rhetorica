"""VictoriaMetrics Prometheus-compatible intent API helpers."""

from __future__ import annotations

import re
from collections.abc import Callable
from string import Template
from typing import Any, cast

import httpx
from rhetorica_backend.contracts.admin_integrations import (
    VictoriaMetricsConnectionConfig,
    VictoriaMetricsTemplateConfig,
)

DEFAULT_USER_AGENT = "Rhetorica VictoriaMetrics Connector"
MAX_ERROR_BODY_CHARS = 1200
PLACEHOLDER_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class VictoriaMetricsRequestError(RuntimeError):
    """Structured VictoriaMetrics HTTP failure safe to expose to orchestration."""

    def __init__(
        self,
        *,
        status_code: int,
        request_path: str,
        request_params: dict[str, Any],
        response_body: str,
    ) -> None:
        super().__init__(
            f"VictoriaMetrics API request failed with HTTP {status_code} for {request_path}"
        )
        self.status_code = status_code
        self.request_path = request_path
        self.request_params = request_params
        self.response_body = response_body

    @classmethod
    def from_response(
        cls,
        response: httpx.Response,
        *,
        request_path: str,
        request_params: dict[str, Any],
    ) -> VictoriaMetricsRequestError:
        return cls(
            status_code=response.status_code,
            request_path=request_path,
            request_params=request_params,
            response_body=response.text[:MAX_ERROR_BODY_CHARS],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "request_path": self.request_path,
            "request_params": self.request_params,
            "response_body": self.response_body,
        }


class VictoriaMetricsService:
    """Read-only VictoriaMetrics API wrapper using admin-owned query templates."""

    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self._client_factory = client_factory

    def list_templates(self, connection: VictoriaMetricsConnectionConfig) -> dict[str, Any]:
        templates = [
            {
                "template_id": template.template_id,
                "title": template.title,
                "use_when": template.use_when,
                "mode": template.mode,
                "allowed_filters": template.allowed_filters,
                "default_step": template.default_step,
                "max_range_seconds": template.max_range_seconds,
                "unit": template.unit,
                "result_limit": template.result_limit,
            }
            for template in connection.templates
        ]
        return {"templates": templates, "count": len(templates)}

    def query_instant(
        self,
        connection: VictoriaMetricsConnectionConfig,
        *,
        template_id: str,
        filters: dict[str, str],
        time: str | None = None,
        series_offset: int = 0,
        series_limit: int | None = None,
    ) -> dict[str, Any]:
        template = self._template(connection, template_id, required_mode="instant")
        query = self._render_query(template, filters)
        params: dict[str, Any] = {"query": query}
        if time:
            params["time"] = time
        payload = self._request_json(connection, connection.query_path, params=params)
        return self._bounded_result(
            payload,
            template=template,
            series_offset=series_offset,
            series_limit=series_limit or min(template.result_limit, connection.max_series),
            point_offset=0,
            point_limit=connection.max_datapoints,
            include_query=False,
        )

    def query_range(
        self,
        connection: VictoriaMetricsConnectionConfig,
        *,
        template_id: str,
        filters: dict[str, str],
        start: str,
        end: str,
        step: str | None = None,
        series_offset: int = 0,
        series_limit: int | None = None,
        point_offset: int = 0,
        point_limit: int | None = None,
    ) -> dict[str, Any]:
        template = self._template(connection, template_id, required_mode="range")
        query = self._render_query(template, filters)
        params = {
            "query": query,
            "start": start,
            "end": end,
            "step": step or template.default_step,
        }
        payload = self._request_json(connection, connection.query_range_path, params=params)
        return self._bounded_result(
            payload,
            template=template,
            series_offset=series_offset,
            series_limit=series_limit or min(template.result_limit, connection.max_series),
            point_offset=point_offset,
            point_limit=point_limit or connection.max_datapoints,
            include_query=False,
        )

    def _request_json(
        self,
        connection: VictoriaMetricsConnectionConfig,
        path: str,
        *,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if connection.auth_type == "bearer" and connection.bearer_token:
            headers["Authorization"] = f"Bearer {connection.bearer_token}"
        request_params = {key: value for key, value in params.items() if value is not None}
        with self._client_factory(timeout=connection.timeout_seconds, verify=connection.verify_tls) as client:
            response = client.get(
                f"{connection.base_url.rstrip('/')}{path}",
                params=request_params,
                headers=headers,
            )
        if response.status_code >= 400:
            raise VictoriaMetricsRequestError.from_response(
                response,
                request_path=path,
                request_params={key: value for key, value in request_params.items() if key != "query"},
            )
        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise VictoriaMetricsRequestError(
                status_code=200,
                request_path=path,
                request_params={key: value for key, value in request_params.items() if key != "query"},
                response_body=str(payload)[:MAX_ERROR_BODY_CHARS],
            )
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _template(
        connection: VictoriaMetricsConnectionConfig,
        template_id: str,
        *,
        required_mode: str,
    ) -> VictoriaMetricsTemplateConfig:
        for template in connection.templates:
            if template.template_id != template_id:
                continue
            if template.mode not in {required_mode, "both"}:
                raise ValueError(f"Template {template_id!r} does not support {required_mode} mode.")
            return template
        raise ValueError(f"VictoriaMetrics template {template_id!r} is not configured.")

    @staticmethod
    def _render_query(template: VictoriaMetricsTemplateConfig, filters: dict[str, str]) -> str:
        allowed = set(template.allowed_filters)
        for key in filters:
            if key not in allowed:
                raise ValueError(f"Filter {key!r} is not allowed for template {template.template_id!r}.")
        placeholders = set(PLACEHOLDER_RE.findall(template.query_template))
        unknown = placeholders - allowed
        if unknown:
            raise ValueError(
                f"Template {template.template_id!r} contains placeholders outside allowed_filters."
            )
        missing = sorted(placeholders - set(filters))
        if missing:
            raise ValueError(
                f"Template {template.template_id!r} requires filters: {', '.join(missing)}."
            )
        safe_filters = {key: VictoriaMetricsService._escape_filter_value(value) for key, value in filters.items()}
        return Template(template.query_template).substitute(safe_filters)

    @staticmethod
    def _bounded_result(
        payload: dict[str, Any],
        *,
        template: VictoriaMetricsTemplateConfig,
        series_offset: int,
        series_limit: int,
        point_offset: int,
        point_limit: int,
        include_query: bool,
    ) -> dict[str, Any]:
        data = VictoriaMetricsService._dict(payload.get("data"))
        result = VictoriaMetricsService._list(data.get("result"))
        bounded_series = result[series_offset : series_offset + series_limit]
        normalized: list[dict[str, Any]] = []
        for series in bounded_series:
            if not isinstance(series, dict):
                continue
            values = series.get("values")
            value = series.get("value")
            item: dict[str, Any] = {"metric": series.get("metric", {})}
            if isinstance(values, list):
                item["values"] = values[point_offset : point_offset + point_limit]
                item["point_offset"] = point_offset
                item["next_point_offset"] = (
                    point_offset + point_limit
                    if point_offset + point_limit < len(values)
                    else None
                )
                item["points_truncated"] = item["next_point_offset"] is not None
                item["point_count"] = len(values)
            elif isinstance(value, list):
                item["value"] = value
            normalized.append(item)
        next_series_offset = (
            series_offset + series_limit
            if series_offset + series_limit < len(result)
            else None
        )
        response = {
            "template_id": template.template_id,
            "title": template.title,
            "unit": template.unit,
            "result_type": data.get("resultType"),
            "series": normalized,
            "series_count": len(result),
            "series_offset": series_offset,
            "next_series_offset": next_series_offset,
            "series_truncated": next_series_offset is not None,
        }
        if include_query:
            response["query"] = payload.get("query")
        return response

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @staticmethod
    def _list(value: Any) -> list[Any]:
        return cast(list[Any], value) if isinstance(value, list) else []
