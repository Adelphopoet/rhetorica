"""Tests for metadata service."""

import json
import logging
from types import SimpleNamespace

from rhetorica_backend.domain.chat.metadata.governance_enrichment_service import (
    OpenMetadataGovernanceEnrichmentService,
)
from rhetorica_backend.domain.chat.metadata.metadata_service import (
    MetadataFetcher,
    MetadataResolver,
    TableContext,
)
from rhetorica_backend.domain.chat.metadata.payload_mapper import MetadataPayloadMapper
from rhetorica_backend.domain.chat.metadata.ranking_service import MetadataRankingService
from rhetorica_backend.domain.chat.metadata.usage_store import InMemoryMetadataUsageStore
from rhetorica_backend.domain.chat.orchestration.tool_execution_support_kernel import (
    ToolExecutionSupportKernel,
)


class _OpenMetadataRepoStub:
    def __init__(self, connections: dict[str | None, object | None]) -> None:
        self._connections = connections

    def get_connection(
        self,
        workspace_id: str,
        name: str | None = None,
    ) -> object | None:
        _ = workspace_id
        return self._connections.get(name)


def test_metadata_resolver_does_not_fallback_to_default_when_requested_connector_missing() -> None:
    resolver = MetadataResolver(
        _OpenMetadataRepoStub(
            {
                None: SimpleNamespace(name="default-openmetadata"),
                "analytics-om": None,
            }
        )
    )

    resolved = resolver.resolve_connection(
        workspace_id="default",
        requested_name="analytics-om",
        fallback_name="analytics-om",
        warnings=[],
    )

    assert resolved is None


def test_search_catalog_preserves_fqn_from_mcp_fqn_field(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name=None,
        database_name=None,
        schema_name=None,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fqn": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                    "databaseSchema": {"name": "dm"},
                }
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="ft_main_whs_partner_date",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["entityType"] == "table"
    assert results[0]["displayName"] == "ft_main_whs_partner_date"
    assert (
        results[0]["fullyQualifiedName"]
        == "Sample-ClickHouse.default.dm.ft_main_whs_partner_date"
    )
    assert results[0]["databaseSchema"] == "dm"
    assert results[0]["relevance_score"] > 0


def test_search_catalog_normalizes_entity_type_case(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name=None,
        database_name=None,
        schema_name=None,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "TABLE",
                    "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
                }
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="ft_main_whs_partner_date",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["entityType"] == "table"


def test_get_entity_details_compacts_table_payload_for_llm(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "entityType": "table",
            "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
            "description": "Unit economics mart by order",
            "tableType": "View",
            "database": {"fullyQualifiedName": "Sample-ClickHouse.default"},
            "databaseSchema": {"name": "dm"},
            "columns": [
                {"name": "ecom_order_pk", "dataTypeDisplay": "nullable<string>"},
                {
                    "name": "cm2_amt",
                    "dataTypeDisplay": "nullable<float64>",
                    "description": "Contribution Margin 2",
                },
            ],
            "extension": {"ddl": "CREATE VIEW ... huge ddl ..."},
            "changeDescription": {"fieldsAdded": ["noise"]},
        },
    )

    result, warnings = fetcher.get_entity_details(
        connection=connection,
        entity_type="table",
        fqn="Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
    )

    assert warnings == []
    assert result == {
        "entity": {
            "entityType": "table",
            "name": "ft_order_unit_economics_v",
            "displayName": "ft_order_unit_economics_v",
            "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
            "description": "Unit economics mart by order",
            "tableType": "View",
            "service": "Sample-ClickHouse",
            "database": "default",
            "databaseSchema": "dm",
            "columns": [
                {"name": "ecom_order_pk", "dataType": "nullable<string>"},
                {
                    "name": "cm2_amt",
                    "dataType": "nullable<float64>",
                    "description": "Contribution Margin 2",
                },
            ],
            "columnNames": ["ecom_order_pk", "cm2_amt"],
        }
    }


def test_get_entity_details_uses_openmetadata_exact_table_schema(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        base_url="https://metadata.example",
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        extra_settings={},
    )

    monkeypatch.setattr(
        fetcher.catalog_gateway,
        "get_openmetadata_table_entity_details",
        lambda **_: (
            {
                "entityType": "table",
                "fullyQualifiedName": "trino_omni.iceberg.dm_ft.ft_order",
                "database": {"fullyQualifiedName": "trino_omni.iceberg"},
                "databaseSchema": {"name": "dm_ft"},
                "columns": [
                    {"name": "ecom_order_pk", "dataType": "BIGINT"},
                    {"name": "order_source", "dataType": "VARCHAR"},
                    {"name": "order_create_dttm", "dataType": "TIMESTAMP"},
                ],
            },
            [],
        ),
    )
    monkeypatch.setattr(
        OpenMetadataGovernanceEnrichmentService,
        "_fetch_quality_payload",
        lambda self, **_: None,
    )

    def _unexpected_mcp_call(**_: object) -> dict[str, object]:
        raise AssertionError("MCP must not be used when exact OpenMetadata schema exists")

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _unexpected_mcp_call)

    result, warnings = fetcher.get_entity_details(
        connection=connection,
        entity_type="table",
        fqn="trino_omni.iceberg.dm_ft.ft_order",
    )

    assert warnings == []
    assert result is not None
    assert result["entity"]["columnNames"] == [
        "ecom_order_pk",
        "order_source",
        "order_create_dttm",
    ]


def test_metadata_mapper_extracts_governance_and_quality_signals() -> None:
    mapper = MetadataPayloadMapper()
    payload = {
        "owners": [
            {"name": "ivan", "type": "user"},
            {"name": "analytics-team", "type": "team"},
        ],
        "domains": [{"fullyQualifiedName": "Business.Sales", "type": "domain"}],
        "tags": [
            {"tagFQN": "Tier.Tier1", "source": "Classification"},
            {"tagFQN": "BusinessGlossary.GMV", "source": "Glossary"},
        ],
        "relatedMetrics": [{"name": "GMV", "fullyQualifiedName": "metrics.GMV"}],
    }
    quality_payload = {
        "data": [
            {"name": "row_count", "testCaseResult": {"testCaseStatus": "Success", "timestamp": 10}},
            {
                "name": "gmv_not_null",
                "testCaseResult": {"testCaseStatus": "Failed", "timestamp": 20},
            },
        ]
    }

    assert mapper.extract_entity_names(payload["owners"]) == ["ivan", "analytics-team"]
    assert mapper.extract_owner_teams(payload["owners"]) == ["analytics-team"]
    assert mapper.extract_glossary_terms(payload)[0].fully_qualified_name == "BusinessGlossary.GMV"
    assert mapper.extract_metric_refs(payload["relatedMetrics"])[0].name == "GMV"
    quality = mapper.extract_quality_summary(quality_payload)
    assert quality is not None
    assert quality.status == "failed"
    assert quality.failed_tests == ("gmv_not_null",)


def test_filter_tables_penalizes_failed_quality_and_marks_governance_matches() -> None:
    mapper = MetadataPayloadMapper()
    service = MetadataRankingService(payload_mapper=mapper)
    quality = mapper.extract_quality_summary({
        "data": [
            {
                "name": "gmv_not_null",
                "testCaseResult": {"testCaseStatus": "Failed", "timestamp": 20},
            }
        ]
    })

    ranked = service.filter_tables(
        tables=[
            TableContext(
                source="openmetadata",
                fqn="Clickhouse.default.dm.ft_gmv_daily",
                columns=["gmv"],
                schema="dm",
                glossary_terms=mapper.extract_glossary_terms(
                    {"tags": [{"tagFQN": "BusinessGlossary.GMV", "source": "Glossary"}]}
                ),
                related_metrics=mapper.extract_metric_refs([{"name": "GMV"}]),
                quality_summary=quality,
            )
        ],
        search="gmv",
        service=None,
        database=None,
        schema=None,
        top_n=5,
    )

    assert ranked[0].quality_summary is not None
    assert "glossary_term_partial" in ranked[0].matched_on
    assert "metric_partial" in ranked[0].matched_on
    assert "quality_failed" in ranked[0].matched_on


def test_get_entity_details_enriches_table_with_quality_summary(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        base_url="https://metadata.example",
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        extra_settings={},
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "entityType": "table",
            "fullyQualifiedName": "Clickhouse.default.dm.ft_gmv_daily",
            "columns": [{"name": "gmv", "dataType": "DOUBLE"}],
        },
    )
    monkeypatch.setattr(
        fetcher.catalog_gateway,
        "get_openmetadata_table_entity_details",
        lambda **_: (None, []),
    )
    monkeypatch.setattr(
        OpenMetadataGovernanceEnrichmentService,
        "_fetch_quality_payload",
        lambda self, **_: {
            "data": [
                {
                    "name": "gmv_not_null",
                    "testCaseResult": {"testCaseStatus": "Failed", "timestamp": 20},
                }
            ]
        },
    )

    result, warnings = fetcher.get_entity_details(
        connection=connection,
        entity_type="table",
        fqn="Clickhouse.default.dm.ft_gmv_daily",
    )

    assert warnings == ["metadata_quality_failed"]
    assert result is not None
    assert result["entity"]["quality_summary"]["status"] == "failed"
    assert result["entity"]["quality_summary"]["failed_tests"] == ["gmv_not_null"]


def test_fetch_tables_merges_duplicate_fqn_from_openmetadata_and_mcp(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        base_url="https://metadata.example",
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        extra_settings={},
        service_name=None,
        database_name=None,
        schema_name=None,
        table_limit=100,
    )

    monkeypatch.setattr(
        fetcher,
        "_fetch_openmetadata_tables",
        lambda _connection: [
            TableContext(
                source="openmetadata",
                fqn="Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
                columns=["ecom_order_pk"],
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
            )
        ],
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_mcp_tables",
        lambda _connection: [
            TableContext(
                source="mcp",
                fqn="Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
                columns=["ecom_order_pk", "cm2_amt", "create_date"],
                column_types={"cm2_amt": "Float64"},
                column_descriptions={"cm2_amt": "Contribution Margin 2"},
                description="Unit economics mart by order",
                service="Sample-ClickHouse",
                database="default",
                schema="dm",
                tags=["Tier.Tier1"],
            )
        ],
    )

    tables, warnings = fetcher.fetch_tables(connection=connection)

    assert warnings == []
    assert len(tables) == 1
    assert tables[0].source == "mcp"
    assert tables[0].columns == ["ecom_order_pk", "cm2_amt", "create_date"]
    assert tables[0].column_types["cm2_amt"] == "Float64"
    assert tables[0].column_descriptions["cm2_amt"] == "Contribution Margin 2"
    assert tables[0].description == "Unit economics mart by order"
    assert tables[0].tags == ["Tier.Tier1"]


def test_filter_tables_prefers_governed_table_over_sandbox() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.sandbox.sales_orders",
            columns=["order_id", "gmv"],
            schema="sandbox",
            database="analytics",
            service="Sample-ClickHouse",
        ),
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            columns=["order_id", "gmv"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Governed sales orders mart",
            owners=["analytics"],
            domains=["sales"],
            tags=["Tier.Tier1", "Certification.Gold"],
            tier="Tier.Tier1",
            certification="Certification.Gold",
            usage_count=140,
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="sales orders",
        service=None,
        database=None,
        schema=None,
        preferred_service="Sample-ClickHouse",
        preferred_database="analytics",
        preferred_schema="dm",
    )

    assert len(ranked) == 1
    assert ranked[0].schema == "dm"
    assert ranked[0].certification == "Certification.Gold"


def test_filter_tables_does_not_record_ranking_results_as_usage() -> None:
    usage_store = InMemoryMetadataUsageStore()
    ranking_service = MetadataRankingService(usage_store=usage_store)
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            columns=["order_id", "gmv"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Governed sales orders mart",
        )
    ]

    ranked = ranking_service.filter_tables(
        tables=tables,
        search="sales orders",
        service=None,
        database=None,
        schema=None,
        workspace_id="ws-1",
        connector_name="om",
    )

    assert [table.fqn for table in ranked] == ["Sample-ClickHouse.analytics.dm.sales_orders"]
    assert usage_store.usage_map(workspace_id="ws-1", connector_name="om") == {}


def test_usage_prior_is_explicit_and_cannot_beat_governed_exact_match() -> None:
    usage_store = InMemoryMetadataUsageStore()
    ranking_service = MetadataRankingService(usage_store=usage_store)
    sandbox = "Sample-ClickHouse.analytics.sandbox.sales_orders"
    governed = "Sample-ClickHouse.analytics.dm.sales_orders"
    for _ in range(40):
        ranking_service.record_successful_usage(
            workspace_id="ws-1",
            connector_name="om",
            table_fqns=[sandbox],
        )

    ranked = ranking_service.filter_tables(
        tables=[
            TableContext(
                source="openmetadata",
                fqn=sandbox,
                columns=["order_id", "gmv"],
                schema="sandbox",
                database="analytics",
                service="Sample-ClickHouse",
            ),
            TableContext(
                source="openmetadata",
                fqn=governed,
                columns=["order_id", "gmv"],
                schema="dm",
                database="analytics",
                service="Sample-ClickHouse",
                description="Governed sales orders mart",
                owners=["analytics"],
                domains=["sales"],
                tags=["Tier.Tier1", "Certification.Gold"],
                tier="Tier.Tier1",
                certification="Certification.Gold",
                usage_count=140,
            ),
        ],
        search="sales_orders",
        service=None,
        database=None,
        schema=None,
        workspace_id="ws-1",
        connector_name="om",
        preferred_service="Sample-ClickHouse",
        preferred_database="analytics",
        preferred_schema="dm",
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [governed, sandbox]
    assert "usage_prior" in ranked[1].matched_on


def test_negative_feedback_pushes_candidate_down() -> None:
    usage_store = InMemoryMetadataUsageStore()
    ranking_service = MetadataRankingService(usage_store=usage_store)
    rejected = "Sample-ClickHouse.analytics.dm.sales_orders_archive"
    accepted = "Sample-ClickHouse.analytics.dm.sales_orders"
    ranking_service.record_negative_feedback(
        workspace_id="ws-1",
        connector_name="om",
        table_fqns=[rejected],
    )

    ranked = ranking_service.filter_tables(
        tables=[
            TableContext(
                source="openmetadata",
                fqn=rejected,
                columns=["order_id"],
                schema="dm",
                database="analytics",
                service="Sample-ClickHouse",
                description="Governed sales orders mart",
            ),
            TableContext(
                source="openmetadata",
                fqn=accepted,
                columns=["order_id"],
                schema="dm",
                database="analytics",
                service="Sample-ClickHouse",
                description="Governed sales orders mart",
            ),
        ],
        search="sales_orders",
        service=None,
        database=None,
        schema=None,
        workspace_id="ws-1",
        connector_name="om",
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [accepted, rejected]


def test_successful_sql_usage_selects_referenced_context_tables() -> None:
    state = SimpleNamespace(
        semantic_grounding=SimpleNamespace(chosen_table_fqn=None),
        context_tables=[
            "Sample-ClickHouse.analytics.dm.sales_orders",
            "Sample-ClickHouse.analytics.dm.customers",
        ],
    )

    table_fqns = ToolExecutionSupportKernel._successful_usage_table_fqns(
        referenced_tables=["dm.sales_orders"],
        state=state,
    )

    assert table_fqns == ["Sample-ClickHouse.analytics.dm.sales_orders"]


def test_filter_tables_prefers_business_dm_over_technical_ods_without_explicit_schema_preference() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.ods.ft_order",
            columns=["order_id", "created_at"],
            schema="ods",
            database="analytics",
            service="Sample-ClickHouse",
            description="Technical operational order feed",
        ),
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.ft_order",
            columns=["order_id", "created_at"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Governed order mart",
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="ft_order",
        service=None,
        database=None,
        schema=None,
        preferred_service="Sample-ClickHouse",
        preferred_database="analytics",
        preferred_schema=None,
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [
        "Sample-ClickHouse.analytics.dm.ft_order",
        "Sample-ClickHouse.analytics.ods.ft_order",
    ]


def test_filter_tables_prefers_canonical_table_over_specialized_variant() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.ft_order_bitrix",
            columns=["order_id"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Bitrix-only orders feed",
        ),
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.ft_order",
            columns=["order_id"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Canonical orders mart",
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="ft_order",
        service=None,
        database=None,
        schema=None,
        preferred_service="Sample-ClickHouse",
        preferred_database="analytics",
        preferred_schema="dm",
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [
        "Sample-ClickHouse.analytics.dm.ft_order",
        "Sample-ClickHouse.analytics.dm.ft_order_bitrix",
    ]
    assert "specialized_variant_penalty" in ranked[1].matched_on


def test_filter_tables_prefers_iceberg_over_hive_for_same_table_name() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="mcp",
            fqn="hive.default.dm_ft.art_price",
            columns=["art_code", "base_price"],
            schema="dm_ft",
            database="default",
            service="hive-dwh",
            description="Historical art price reference",
            owners=["foodtech"],
            domains=["foodtech"],
        ),
        TableContext(
            source="mcp",
            fqn="DWH-iceberg.default.dm_ft.art_price",
            columns=["art_code", "base_price"],
            schema="dm_ft",
            database="default",
            service="DWH-iceberg",
            description="Historical art price reference",
            owners=["foodtech"],
            domains=["foodtech"],
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="art_price",
        service=None,
        database=None,
        schema=None,
        preferred_service=None,
        preferred_database=None,
        preferred_schema=None,
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [
        "DWH-iceberg.default.dm_ft.art_price",
        "hive.default.dm_ft.art_price",
    ]
    assert "engine_iceberg" in ranked[0].matched_on
    assert "engine_hive" in ranked[1].matched_on


def test_filter_tables_prefers_dm_ft_over_stg_ft_with_same_engine() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="mcp",
            fqn="DWH-iceberg.default.stg_ft.art_price",
            columns=["art_code", "base_price"],
            schema="stg_ft",
            database="default",
            service="DWH-iceberg",
            description="Historical art price reference",
            owners=["foodtech"],
            domains=["foodtech"],
        ),
        TableContext(
            source="mcp",
            fqn="DWH-iceberg.default.dm_ft.art_price",
            columns=["art_code", "base_price"],
            schema="dm_ft",
            database="default",
            service="DWH-iceberg",
            description="Historical art price reference",
            owners=["foodtech"],
            domains=["foodtech"],
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="art_price",
        service=None,
        database=None,
        schema=None,
        preferred_service=None,
        preferred_database=None,
        preferred_schema=None,
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [
        "DWH-iceberg.default.dm_ft.art_price",
        "DWH-iceberg.default.stg_ft.art_price",
    ]
    assert "business_schema" in ranked[0].matched_on
    assert "technical_schema" in ranked[1].matched_on


def test_filter_tables_treats_suffix_dm_as_business_schema() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="mcp",
            fqn="DWH-iceberg.default.orders_stg.art_price",
            columns=["art_code"],
            schema="orders_stg",
            database="default",
            service="DWH-iceberg",
            description="Technical art price table",
        ),
        TableContext(
            source="mcp",
            fqn="DWH-iceberg.default.orders_dm.art_price",
            columns=["art_code"],
            schema="orders_dm",
            database="default",
            service="DWH-iceberg",
            description="Business art price mart",
        ),
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="art_price",
        service=None,
        database=None,
        schema=None,
        preferred_service=None,
        preferred_database=None,
        preferred_schema=None,
        top_n=2,
    )

    assert [table.fqn for table in ranked] == [
        "DWH-iceberg.default.orders_dm.art_price",
        "DWH-iceberg.default.orders_stg.art_price",
    ]


def test_search_catalog_includes_governance_signals_and_reorders(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="analytics",
        schema_name="dm",
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.analytics.sandbox.sales_orders",
                    "database": {"name": "analytics"},
                    "databaseSchema": {"name": "sandbox"},
                    "columnNames": ["order_id", "gmv"],
                },
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.analytics.dm.sales_orders",
                    "database": {"name": "analytics"},
                    "databaseSchema": {"name": "dm"},
                    "columnNames": ["order_id", "gmv"],
                    "description": "Governed sales mart",
                    "owners": [{"name": "analytics"}],
                    "domains": [{"name": "sales"}],
                    "tags": [{"tagFQN": "Certification.Gold"}, {"tagFQN": "Tier.Tier1"}],
                    "usageSummary": {"dailyStats": {"count": 120}},
                },
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="sales orders",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["databaseSchema"] == "dm"
    assert results[0]["certification"] == "Certification.Gold"
    assert results[0]["tier"] == "Tier.Tier1"
    assert results[0]["usage_count"] == 120
    assert results[0]["relevance_score"] > results[1]["relevance_score"]


def test_search_catalog_exact_identifier_beats_semantic_variant(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Trino_OMNI",
        database_name="iceberg",
        schema_name="dm_ft",
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.dm_ft.art_price_online_frmt_date",
                    "database": {"name": "iceberg"},
                    "databaseSchema": {"name": "dm_ft"},
                    "columnNames": ["art_code", "price", "price_date"],
                    "description": "Governed enriched price mart",
                    "owners": [{"name": "FoodTech", "type": "team"}],
                    "tags": [{"tagFQN": "Certification.Gold"}, {"tagFQN": "Tier.Tier1"}],
                },
                {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.stg_ft.art_price",
                    "database": {"name": "iceberg"},
                    "databaseSchema": {"name": "stg_ft"},
                    "columnNames": ["art_code", "base_price"],
                },
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="art_price",
        entity_type="table",
        limit=2,
        offset=0,
    )

    assert warnings == []
    assert results[0]["fullyQualifiedName"] == "trino_omni.iceberg.stg_ft.art_price"
    assert results[0]["identifier_match"] == "exact_leaf"
    assert results[1]["identifier_match"] == "prefix_leaf"


def test_search_catalog_reads_top_level_governance_payload(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name=None,
        database_name=None,
        schema_name=None,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.dm_ft.art_price",
                    "database": {"name": "iceberg"},
                    "databaseSchema": {"name": "dm_ft"},
                    "tier": {"tagFQN": "Tier.Tier1"},
                    "certification": {"tagLabel": {"tagFQN": "Certification.Gold"}},
                },
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="art_price",
        entity_type="table",
        limit=1,
        offset=0,
    )

    assert warnings == []
    assert results[0]["tier"] == "Tier.Tier1"
    assert results[0]["certification"] == "Certification.Gold"


def test_search_catalog_short_price_keyword_prefers_governed_art_price(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Trino_OMNI",
        database_name="iceberg",
        schema_name="dm_ft",
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "greenplum-analytics-prod.dwh.ft_pricing_prod.prices",
                    "database": {"name": "dwh"},
                    "databaseSchema": {"name": "ft_pricing_prod"},
                    "owners": [{"name": "FoodTech", "type": "team"}],
                },
                {
                    "entityType": "table",
                    "fullyQualifiedName": "trino_omni.iceberg.dm_ft.art_price",
                    "database": {"name": "iceberg"},
                    "databaseSchema": {"name": "dm_ft"},
                    "columnNames": ["art_code", "base_price"],
                    "description": "Исторический справочник цен товаров в магазинах",
                    "owners": [{"name": "FoodTech", "type": "team"}],
                    "tier": {"tagFQN": "Tier.Tier1"},
                    "certification": {"tagLabel": {"tagFQN": "Certification.Gold"}},
                },
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="price",
        entity_type="table",
        limit=2,
        offset=0,
    )

    assert warnings == []
    assert results[0]["fullyQualifiedName"] == "trino_omni.iceberg.dm_ft.art_price"
    assert results[0]["identifier_match"] == "partial_leaf"
    assert results[0]["certification"] == "Certification.Gold"


def test_search_catalog_oversamples_beyond_requested_limit(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="analytics",
        schema_name=None,
    )
    requested_sizes: list[int] = []

    def _fake_mcp_call_tool(**kwargs):
        requested_sizes.append(kwargs["arguments"]["size"])
        return {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.analytics.ods.ft_order",
                    "database": {"name": "analytics"},
                    "databaseSchema": {"name": "ods"},
                },
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.analytics.dm.ft_order",
                    "database": {"name": "analytics"},
                    "databaseSchema": {"name": "dm"},
                    "description": "Governed orders mart",
                },
            ]
        }

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="ft_order",
        entity_type="table",
        limit=1,
        offset=0,
    )

    assert warnings == []
    assert requested_sizes[0] > 1
    assert results[0]["fullyQualifiedName"] == "Sample-ClickHouse.analytics.dm.ft_order"


def test_build_mcp_query_filter_uses_identifier_aware_clauses() -> None:
    payload = json.loads(
        MetadataFetcher._build_mcp_query_filter(entity_type="table", query="ft_order")
    )

    must = payload["bool"]["must"]
    assert {"term": {"entityType": "table"}} in must
    search_clause = next(
        clause["bool"]
        for clause in must
        if isinstance(clause, dict) and isinstance(clause.get("bool"), dict)
    )
    assert search_clause["minimum_should_match"] == 1
    assert {"term": {"name.keyword": "ft_order"}} in search_clause["should"]
    assert {"term": {"displayName.keyword": "ft_order"}} in search_clause["should"]
    assert {"match": {"fullyQualifiedName": "ft_order"}} in search_clause["should"]
    assert {"match": {"name.ngram": "ft_order"}} in search_clause["should"]


def test_build_mcp_query_filter_uses_explicit_schema_clause() -> None:
    payload = json.loads(
        MetadataFetcher._build_mcp_query_filter(
            entity_type="table",
            query="city",
            schema="dict",
        )
    )

    must = payload["bool"]["must"]
    assert {"term": {"entityType": "table"}} in must
    assert {
        "bool": {
            "should": [
                {"term": {"databaseSchema.name.keyword": "dict"}},
                {"term": {"databaseSchema.displayName.keyword": "dict"}},
            ],
            "minimum_should_match": 1,
        }
    } in must
    search_clause = next(
        clause["bool"]
        for clause in must
        if isinstance(clause, dict)
        and isinstance(clause.get("bool"), dict)
        and {"term": {"name.keyword": "city"}} in clause["bool"].get("should", [])
    )
    assert {"term": {"name.keyword": "city"}} in search_clause["should"]
    assert {"match": {"fullyQualifiedName": "city"}} in search_clause["should"]


def test_build_mcp_query_filter_defaults_to_primary_discovery_entity_types() -> None:
    payload = json.loads(
        MetadataFetcher._build_mcp_query_filter(
            entity_type=None,
            query="цена товара price product",
        )
    )

    must = payload["bool"]["must"]
    entity_clause = must[0]["bool"]
    assert {"term": {"entityType": "table"}} in entity_clause["should"]
    assert {"term": {"entityType": "glossaryTerm"}} in entity_clause["should"]
    assert {"term": {"entityType": "metric"}} in entity_clause["should"]
    assert {"term": {"entityType": "container"}} not in entity_clause["should"]
    search_clause = must[-1]["bool"]
    assert search_clause["minimum_should_match"] == 1
    assert {"match": {"name": "price"}} in search_clause["should"]
    assert {"match": {"columns.name": "product"}} in search_clause["should"]


def test_search_catalog_passes_identifier_aware_query_filter(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name="dm",
    )
    captured_arguments: list[dict[str, object]] = []

    def _fake_mcp_call_tool(**kwargs):
        captured_arguments.append(kwargs["arguments"])
        return {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order",
                    "database": {"name": "default"},
                    "databaseSchema": {"name": "dm"},
                    "description": "Canonical orders mart",
                }
            ]
        }

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="ft_order",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["fullyQualifiedName"] == "Sample-ClickHouse.default.dm.ft_order"
    assert captured_arguments[0]["query"] == "*ft_order*"
    assert "certification" in str(captured_arguments[0]["fields"])
    query_filter = json.loads(str(captured_arguments[0]["queryFilter"]))
    search_clause = next(
        clause["bool"]
        for clause in query_filter["bool"]["must"]
        if isinstance(clause, dict) and isinstance(clause.get("bool"), dict)
    )
    assert {"term": {"name.keyword": "ft_order"}} in search_clause["should"]
    assert {"match": {"name.ngram": "ft_order"}} in search_clause["should"]


def test_search_catalog_wraps_single_keyword_query_for_partial_match(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name=None,
        database_name=None,
        schema_name=None,
    )
    captured_arguments: list[dict[str, object]] = []

    def _fake_mcp_call_tool(**kwargs):
        captured_arguments.append(kwargs["arguments"])
        return {"results": []}

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    fetcher.search_catalog(
        connection=connection,
        query="price",
        entity_type=None,
        limit=5,
        offset=0,
    )

    assert captured_arguments[0]["query"] == "*price*"
    assert captured_arguments[0]["entityType"] == "table"


def test_search_catalog_uses_match_all_query_for_multi_token_discovery(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name=None,
        database_name=None,
        schema_name=None,
    )
    captured_arguments: list[dict[str, object]] = []

    def _fake_mcp_call_tool(**kwargs):
        captured_arguments.append(kwargs["arguments"])
        return {"results": []}

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    fetcher.search_catalog(
        connection=connection,
        query="цена товара price product",
        entity_type=None,
        limit=5,
        offset=0,
    )

    assert captured_arguments[0]["query"] == "*"
    assert "entityType" not in captured_arguments[0]


def test_search_catalog_passes_explicit_schema_filter(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )
    captured_arguments: list[dict[str, object]] = []

    def _fake_mcp_call_tool(**kwargs):
        captured_arguments.append(kwargs["arguments"])
        return {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.default.dict.city",
                    "database": {"name": "default"},
                    "databaseSchema": {"name": "dict"},
                    "description": "City dictionary",
                }
            ]
        }

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="city",
        entity_type="table",
        schema="dict",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["fullyQualifiedName"] == "Sample-ClickHouse.default.dict.city"
    query_filter = json.loads(str(captured_arguments[0]["queryFilter"]))
    assert {
        "bool": {
            "should": [
                {"term": {"databaseSchema.name.keyword": "dict"}},
                {"term": {"databaseSchema.displayName.keyword": "dict"}},
            ],
            "minimum_should_match": 1,
        }
    } in query_filter["bool"]["must"]


def test_filter_tables_drops_unmatched_preferred_scope_noise() -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.default.imp_prod.mag_collect_service_assemblies",
            columns=["assembly_id"],
            schema="imp_prod",
            database="default",
            service="Sample-ClickHouse",
            description="Operational service assemblies",
            owners=["ops"],
        )
    ]

    ranked = fetcher.filter_tables(
        tables=tables,
        search="ft_order",
        service=None,
        database=None,
        schema=None,
        preferred_service="Sample-ClickHouse",
        preferred_database="default",
        preferred_schema=None,
    )

    assert ranked == []


def test_search_catalog_drops_zero_relevance_identifier_results(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.default.imp_prod.mag_collect_service_assemblies",
                    "database": {"name": "default"},
                    "databaseSchema": {"name": "imp_prod"},
                    "columnNames": ["assembly_id"],
                    "description": "Operational service assemblies",
                    "owners": [{"name": "ops"}],
                }
            ]
        },
    )

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="ft_order",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results == []


def test_search_catalog_ranked_results_keep_zero_relevance_compactly(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name=None,
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.default.imp_prod.mag_collect_service_assemblies",
                    "database": {"name": "default"},
                    "databaseSchema": {"name": "imp_prod"},
                    "columnNames": ["assembly_id"],
                    "description": "Operational service assemblies",
                    "owners": [{"name": "ops"}],
                }
            ]
        },
    )

    results, warnings, ranked_results = fetcher.search_catalog_with_ranked_results(
        connection=connection,
        query="ft_order",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results == []
    assert ranked_results[0]["rank"] == 1
    assert ranked_results[0]["entityType"] == "table"
    assert ranked_results[0]["fullyQualifiedName"] == (
        "Sample-ClickHouse.default.imp_prod.mag_collect_service_assemblies"
    )
    assert ranked_results[0]["database"] == "default"
    assert ranked_results[0]["databaseSchema"] == "imp_prod"
    assert ranked_results[0]["relevance_score"] == 0.0
    assert ranked_results[0]["identifier_match"] == "none"
    assert ranked_results[0]["description"] == "Operational service assemblies"
    assert "columnNames" not in ranked_results[0]


def test_search_catalog_expands_metric_relation_to_governed_table(monkeypatch) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="default",
        schema_name="dm",
    )

    def _fake_mcp_call_tool(**kwargs):
        tool_name = kwargs["tool_name"]
        arguments = kwargs["arguments"]
        if tool_name == "search_metadata":
            entity_type = arguments.get("entityType")
            if entity_type == "table":
                return {"results": []}
            if entity_type == "metric":
                return {
                    "results": [
                        {
                            "entityType": "metric",
                            "fullyQualifiedName": "business.orders_count",
                            "displayName": "Orders",
                            "description": "Business metric for order count",
                        }
                    ]
                }
            if entity_type == "glossaryTerm":
                return {"results": []}
        if tool_name == "get_entity_details":
            return {
                "entity": {
                    "entityType": "metric",
                    "fullyQualifiedName": "business.orders_count",
                    "table": {
                        "entityType": "table",
                        "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order",
                        "service": {"name": "Sample-ClickHouse"},
                        "database": {"name": "default"},
                        "databaseSchema": {"name": "dm"},
                        "description": "Canonical orders mart",
                    },
                }
            }
        if tool_name == "get_entity_lineage":
            return {"lineage": {}}
        return {"results": []}

    monkeypatch.setattr(fetcher, "_mcp_call_tool", _fake_mcp_call_tool)

    results, warnings = fetcher.search_catalog(
        connection=connection,
        query="количество заказов",
        entity_type="table",
        limit=5,
        offset=0,
    )

    assert warnings == []
    assert results[0]["fullyQualifiedName"] == "Sample-ClickHouse.default.dm.ft_order"
    assert "related_metric" in results[0]["matched_on"]


def test_filter_tables_logs_ranking_summary_and_candidate_breakdown(caplog) -> None:
    fetcher = MetadataFetcher()
    tables = [
        TableContext(
            source="openmetadata",
            fqn="Sample-ClickHouse.analytics.dm.sales_orders",
            columns=["order_id", "gmv"],
            schema="dm",
            database="analytics",
            service="Sample-ClickHouse",
            description="Governed sales orders mart",
            owners=["analytics"],
            domains=["sales"],
            tags=["Tier.Tier1", "Certification.Gold"],
            tier="Tier.Tier1",
            certification="Certification.Gold",
            usage_count=140,
        )
    ]

    with caplog.at_level(logging.DEBUG):
        ranked = fetcher.filter_tables(
            tables=tables,
            search="sales orders",
            service=None,
            database=None,
            schema=None,
            workspace_id="ws-1",
            connector_name="om",
            preferred_service="Sample-ClickHouse",
            preferred_database="analytics",
            preferred_schema="dm",
        )

    assert len(ranked) == 1
    assert any(record.message == "metadata.table_ranking.started" for record in caplog.records)
    candidate_record = next(
        record for record in caplog.records if record.message == "metadata.table_ranking.candidate_scored"
    )
    assert candidate_record.table_fqn == "Sample-ClickHouse.analytics.dm.sales_orders"
    assert candidate_record.score_breakdown["governance"] > 0
    completed_record = next(
        record for record in caplog.records if record.message == "metadata.table_ranking.completed"
    )
    assert completed_record.selected_count == 1


def test_search_catalog_logs_ranked_table_candidates(monkeypatch, caplog) -> None:
    fetcher = MetadataFetcher()
    connection = SimpleNamespace(
        mcp_tables_url="https://metadata.example/mcp",
        api_token=None,
        verify_tls=True,
        fields="columns,database,databaseSchema",
        service_name="Sample-ClickHouse",
        database_name="analytics",
        schema_name="dm",
    )

    monkeypatch.setattr(
        fetcher,
        "_mcp_call_tool",
        lambda **_: {
            "results": [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "Sample-ClickHouse.analytics.dm.sales_orders",
                    "database": {"name": "analytics"},
                    "databaseSchema": {"name": "dm"},
                    "columnNames": ["order_id", "gmv"],
                    "description": "Governed sales mart",
                    "owners": [{"name": "analytics"}],
                    "domains": [{"name": "sales"}],
                    "tags": [{"tagFQN": "Certification.Gold"}, {"tagFQN": "Tier.Tier1"}],
                }
            ]
        },
    )

    with caplog.at_level(logging.DEBUG):
        results, warnings = fetcher.search_catalog(
            connection=connection,
            query="sales orders",
            entity_type="table",
            limit=5,
            offset=0,
        )

    assert warnings == []
    assert len(results) == 1
    candidate_record = next(
        record for record in caplog.records if record.message == "metadata.catalog_ranking.candidate_scored"
    )
    assert candidate_record.table_fqn == "Sample-ClickHouse.analytics.dm.sales_orders"
    completed_record = next(
        record for record in caplog.records if record.message == "metadata.catalog_ranking.completed"
    )
    assert completed_record.selected_count == 1
