from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing import (
    SqlAnswerPostprocessingService,
)


def _trino_connector() -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id="conn-trino",
        name="trino-adhoc",
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine="trino",
            host="trino.local",
            port=443,
            username="analyst",
            catalog="iceberg",
            schema="iceberg",
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )


def _impala_connector() -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id="conn-impala",
        name="impala-prod",
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine="impala",
            host="impala.local",
            port=21050,
            username="analyst",
            database="default",
            schema="default",
        ),
        has_password=True,
        created_at=now,
        updated_at=now,
    )


def test_sql_answer_postprocessing_service_uses_selected_connector_dialect(
    monkeypatch,
) -> None:
    connector = _trino_connector()
    service = SqlAnswerPostprocessingService(
        sql_router=SimpleNamespace(select_connector=lambda **_kwargs: connector)
    )
    seen_dialects: list[str] = []

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing.SqlGuardService.guard",
        lambda _self, request: seen_dialects.append(request.dialect)
        or SimpleNamespace(allowed=True, violations=[]),
    )

    payload = service.postprocess(
        request=SimpleNamespace(
            conversation_id=None,
            database_connector_name="trino-adhoc",
        ),
        llm_payload={"sql": "SELECT * FROM UNNEST(ARRAY[1, 2]) AS t(x)"},
        db_connectors=[connector],
        warnings=[],
    )

    assert payload["sql"] == "SELECT * FROM UNNEST(ARRAY[1, 2]) AS t(x)"
    assert seen_dialects == ["trino"]


def test_sql_answer_postprocessing_service_maps_impala_to_hive_dialect(
    monkeypatch,
) -> None:
    connector = _impala_connector()
    service = SqlAnswerPostprocessingService(
        sql_router=SimpleNamespace(select_connector=lambda **_kwargs: connector)
    )
    seen_dialects: list[str] = []

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing.SqlGuardService.guard",
        lambda _self, request: seen_dialects.append(request.dialect)
        or SimpleNamespace(allowed=True, violations=[]),
    )

    payload = service.postprocess(
        request=SimpleNamespace(
            conversation_id=None,
            database_connector_name="impala-prod",
        ),
        llm_payload={"sql": "SELECT 1"},
        db_connectors=[connector],
        warnings=[],
    )

    assert payload["sql"] == "SELECT 1"
    assert seen_dialects == ["hive"]


def test_sql_answer_postprocessing_service_blocks_mutating_sql(monkeypatch) -> None:
    connector = _trino_connector()
    service = SqlAnswerPostprocessingService(
        sql_router=SimpleNamespace(select_connector=lambda **_kwargs: connector)
    )

    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.orchestration.transport_payload_postprocessing.SqlGuardService.guard",
        lambda *_args, **_kwargs: SimpleNamespace(
            allowed=False,
            violations=[SimpleNamespace(code="NON_READ_ONLY")],
        ),
    )

    warnings: list[str] = []
    payload = service.postprocess(
        request=SimpleNamespace(conversation_id=None, database_connector_name=None),
        llm_payload={"sql": "DELETE FROM dm.sales_orders"},
        db_connectors=[connector],
        warnings=warnings,
    )

    assert payload["sql"] is None
    assert payload["uncertainty_flags"] == ["unsafe_sql_request_blocked"]
    assert warnings == ["unsafe_sql_generation_blocked"]
