from __future__ import annotations

from typing import Any

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import (
    VictoriaMetricsConnectionConfig,
    VictoriaMetricsTemplateConfig,
)
from rhetorica_backend.domain.chat.victoriametrics import (
    VictoriaMetricsRequestError,
    VictoriaMetricsService,
)


def _connection() -> VictoriaMetricsConnectionConfig:
    return VictoriaMetricsConnectionConfig(
        provider="victoriametrics",
        base_url="https://vm.example.com/",
        bearer_token="secret-token",
        use_when="Use for metrics diagnostics.",
        auth_type="bearer",
        verify_tls=False,
        query_path="/prometheus/api/v1/query",
        query_range_path="/prometheus/api/v1/query_range",
        templates=[
            VictoriaMetricsTemplateConfig(
                template_id="pod_restarts",
                title="Pod restarts",
                use_when="Inspect pod restart count.",
                query_template='sum by (pod) (kube_pod_container_status_restarts_total{namespace="$namespace"})',
                mode="both",
                allowed_filters=["namespace"],
                default_step="60s",
                max_range_seconds=86400,
                unit="count",
                result_limit=10,
            )
        ],
        timeout_seconds=8,
        max_series=5,
        max_datapoints=3,
    )


def _service(handler) -> VictoriaMetricsService:
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    return VictoriaMetricsService(client_factory=client_factory)


def test_victoriametrics_templates_do_not_expose_raw_promql() -> None:
    payload = VictoriaMetricsService().list_templates(_connection())

    assert payload["count"] == 1
    assert payload["templates"][0]["template_id"] == "pod_restarts"
    assert "query_template" not in payload["templates"][0]


def test_victoriametrics_range_uses_template_and_bounds_output_without_query_echo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.url.path == "/prometheus/api/v1/query_range"
        assert request.url.params["query"] == (
            'sum by (pod) (kube_pod_container_status_restarts_total{namespace="prod"})'
        )
        assert request.url.params["step"] == "60s"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"pod": "api-1"},
                            "values": [[1, "1"], [2, "2"], [3, "3"], [4, "4"]],
                        },
                        {"metric": {"pod": "api-2"}, "values": [[1, "0"]]},
                    ],
                },
            },
        )

    payload = _service(handler).query_range(
        _connection(),
        template_id="pod_restarts",
        filters={"namespace": "prod"},
        start="2026-06-05T00:00:00Z",
        end="2026-06-05T01:00:00Z",
        point_limit=2,
        series_limit=1,
    )

    assert "query" not in payload
    assert payload["series_count"] == 2
    assert payload["next_series_offset"] == 1
    assert payload["series"][0]["values"] == [[1, "1"], [2, "2"]]
    assert payload["series"][0]["next_point_offset"] == 2
    assert payload["series"][0]["points_truncated"] is True


def test_victoriametrics_rejects_unconfigured_filters_and_structures_http_errors() -> None:
    with pytest.raises(ValueError, match="Filter 'pod' is not allowed"):
        VictoriaMetricsService().query_instant(
            _connection(),
            template_id="pod_restarts",
            filters={"namespace": "prod", "pod": "api-1"},
        )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with pytest.raises(VictoriaMetricsRequestError) as exc_info:
        _service(handler).query_instant(
            _connection(),
            template_id="pod_restarts",
            filters={"namespace": "prod"},
        )

    payload = exc_info.value.to_payload()
    assert payload["status_code"] == 503
    assert payload["request_path"] == "/prometheus/api/v1/query"
    assert "query" not in payload["request_params"]
    assert payload["response_body"] == "down"
