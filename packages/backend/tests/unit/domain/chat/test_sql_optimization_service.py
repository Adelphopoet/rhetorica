from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillItem,
)
from rhetorica_backend.domain.chat.sql.optimization import (
    ClickHouseInspectionResult,
    SqlOptimizationService,
    TableOptimizationMetadata,
)
from rhetorica_backend.domain.chat.sql.semantic_preflight import SqlPreflightResult


class _PreflightServiceStub:
    def __init__(self, status: str) -> None:
        self._status = status
        self.calls: list[str] = []

    def check(self, **kwargs) -> SqlPreflightResult:
        self.calls.append(str(kwargs["sql"]))
        return SqlPreflightResult(status=self._status)


class _WorkspaceSkillRepositoryStub:
    def __init__(self, skills_by_engine: dict[str, list[WorkspaceSkillItem]]) -> None:
        self._skills_by_engine = skills_by_engine
        self.calls: list[str] = []

    def list_active_for_capability(
        self,
        *,
        workspace_id: str,
        capability_family: str,
        engine_scope: str,
        limit: int = 20,
    ) -> list[WorkspaceSkillItem]:
        _ = workspace_id, capability_family, limit
        self.calls.append(engine_scope)
        return list(self._skills_by_engine.get(engine_scope, []))


def _connection() -> EngineConnectionConfig:
    return EngineConnectionConfig(
        engine="clickhouse",
        host="localhost",
        port=8123,
        username="default",
        password="secret",
        database="analytics",
        secure=False,
        verify=False,
    )


def _empty_inspection() -> ClickHouseInspectionResult:
    return ClickHouseInspectionResult(tables=[], evidence_used=[])


def _skill_with_rule(rule_id: str) -> WorkspaceSkillItem:
    now = datetime.now(tz=UTC)
    return WorkspaceSkillItem(
        id="skill-1",
        workspace_id="default",
        name="clickhouse-best-practices",
        source_url=(
            "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
            "skills/clickhouse-best-practices/SKILL.md"
        ),
        content_bundle=WorkspaceSkillBundle(
            assets=[
                WorkspaceSkillAsset(
                    path=f"skills/clickhouse-best-practices/rules/{rule_id}.md",
                    source_url=(
                        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
                        f"skills/clickhouse-best-practices/rules/{rule_id}.md"
                    ),
                    kind="rule",
                    title="Rule",
                    summary="Rule summary.",
                    rule_id=rule_id,
                    impact="CRITICAL",
                    content_markdown="## Rule\n\nRule summary.\n",
                )
            ]
        ),
        created_at=now,
        updated_at=now,
    )


def _trino_connection() -> EngineConnectionConfig:
    return EngineConnectionConfig(
        engine="trino",
        host="localhost",
        port=443,
        username="analyst",
        password="secret",
        catalog="iceberg",
        schema="dm_ft",
        secure=True,
        verify=True,
    )


def _greenplum_connection() -> EngineConnectionConfig:
    return EngineConnectionConfig(
        engine="greenplum",
        host="localhost",
        port=5432,
        username="gpadmin",
        password="secret",
        database="analytics",
        secure=False,
        verify=False,
    )


def test_optimizer_skips_when_supported_engine_has_no_matching_skills() -> None:
    repository = _WorkspaceSkillRepositoryStub(
        skills_by_engine={
            "clickhouse": [_skill_with_rule("query-join-filter-before")],
        }
    )
    service = SqlOptimizationService(workspace_skill_repository=repository)

    assert (
        service.should_optimize(
            workspace_id="default",
            connection=_trino_connection(),
            selected_connector_name="trino-adhoc",
        )
        is False
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_trino_connection(),
        sql="SELECT o.id FROM analytics.orders AS o",
        referenced_tables=["analytics.orders"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders"],
        metadata_table_columns={"analytics.orders": {"id"}},
        selected_connector_name="trino-adhoc",
    )

    assert outcome.trace.optimizer_used is False
    assert "sql_optimizer_no_matching_skills" in outcome.trace.warnings
    assert repository.calls[-1] == "trino"


def test_optimizer_skips_when_supported_clickhouse_has_no_matching_skills() -> None:
    repository = _WorkspaceSkillRepositoryStub(skills_by_engine={"trino": []})
    service = SqlOptimizationService(workspace_skill_repository=repository)

    assert (
        service.should_optimize(
            workspace_id="default",
            connection=_connection(),
            selected_connector_name="clickhouse-main",
        )
        is False
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql="SELECT o.id FROM analytics.orders AS o",
        referenced_tables=["analytics.orders"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders"],
        metadata_table_columns={"analytics.orders": {"id"}},
        selected_connector_name="clickhouse-main",
    )

    assert outcome.trace.optimizer_used is False
    assert "sql_optimizer_no_matching_skills" in outcome.trace.warnings
    assert repository.calls[-1] == "clickhouse"


def test_optimizer_uses_trino_skill_scope_without_clickhouse_probes(monkeypatch) -> None:
    repository = _WorkspaceSkillRepositoryStub(
        skills_by_engine={
            "trino": [_skill_with_rule("query-join-filter-before")],
        }
    )
    service = SqlOptimizationService(workspace_skill_repository=repository)
    monkeypatch.setattr(
        service,
        "_inspect_clickhouse_tables",
        lambda **_: (_ for _ in ()).throw(AssertionError("clickhouse inspection must not run for trino")),
    )
    monkeypatch.setattr(
        service,
        "_explain_indexes",
        lambda **_: (_ for _ in ()).throw(AssertionError("clickhouse explain must not run for trino")),
    )

    assert (
        service.should_optimize(
            workspace_id="default",
            connection=_trino_connection(),
            selected_connector_name="trino-adhoc",
        )
        is True
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_trino_connection(),
        sql=(
            "SELECT o.id, c.id "
            "FROM iceberg.analytics.orders AS o "
            "INNER JOIN iceberg.analytics.customers AS c ON o.customer_id = c.id "
            "WHERE CAST(o.order_ts AS DATE) >= DATE '2026-04-01'"
        ),
        referenced_tables=["iceberg.analytics.orders", "iceberg.analytics.customers"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["iceberg.analytics.orders", "iceberg.analytics.customers"],
        metadata_table_columns={
            "iceberg.analytics.orders": {"id", "customer_id", "order_ts"},
            "iceberg.analytics.customers": {"id"},
        },
        selected_connector_name="trino-adhoc",
    )

    assert outcome.trace.optimizer_used is True
    assert outcome.trace.optimizer_provider == "trino_rule_advisor"
    assert outcome.trace.engine_scope == "trino"
    assert outcome.trace.skills_used == ["clickhouse-best-practices"]


def test_optimizer_uses_greenplum_skill_scope_without_clickhouse_probes(monkeypatch) -> None:
    repository = _WorkspaceSkillRepositoryStub(
        skills_by_engine={
            "greenplum": [_skill_with_rule("query-join-filter-before")],
        }
    )
    service = SqlOptimizationService(workspace_skill_repository=repository)
    monkeypatch.setattr(
        service,
        "_inspect_clickhouse_tables",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("clickhouse inspection must not run for greenplum")
        ),
    )
    monkeypatch.setattr(
        service,
        "_explain_indexes",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("clickhouse explain must not run for greenplum")
        ),
    )

    assert (
        service.should_optimize(
            workspace_id="default",
            connection=_greenplum_connection(),
            selected_connector_name="greenplum-main",
        )
        is True
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_greenplum_connection(),
        sql=(
            "SELECT o.id, c.id "
            "FROM analytics.orders AS o "
            "INNER JOIN analytics.customers AS c ON o.customer_id = c.id "
            "WHERE DATE(o.order_ts) >= DATE '2026-04-01'"
        ),
        referenced_tables=["analytics.orders", "analytics.customers"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders", "analytics.customers"],
        metadata_table_columns={
            "analytics.orders": {"id", "customer_id", "order_ts"},
            "analytics.customers": {"id"},
        },
        selected_connector_name="greenplum-main",
    )

    assert outcome.trace.optimizer_used is True
    assert outcome.trace.optimizer_provider == "greenplum_rule_advisor"
    assert outcome.trace.engine_scope == "greenplum"
    assert outcome.trace.skills_used == ["clickhouse-best-practices"]


def test_optimizer_applies_safe_join_pushdown(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(service, "_inspect_clickhouse_tables", lambda **_: _empty_inspection())
    monkeypatch.setattr(service, "_explain_indexes", lambda **_: None)
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])

    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=(
            "SELECT o.id, c.id "
            "FROM analytics.orders AS o "
            "INNER JOIN analytics.customers AS c ON o.customer_id = c.id "
            "WHERE o.order_date >= toDate('2026-04-01')"
        ),
        referenced_tables=["analytics.orders", "analytics.customers"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders", "analytics.customers"],
        metadata_table_columns={
            "analytics.orders": {"id", "customer_id", "order_date"},
            "analytics.customers": {"id"},
        },
        selected_connector_name="clickhouse-main",
    )

    assert outcome.trace.optimizer_used is True
    assert outcome.trace.applied is True
    assert outcome.sql_for_execution != outcome.trace.input_sql
    assert "\nFROM" in outcome.sql_for_execution
    assert outcome.trace.optimized_sql_draft == outcome.sql_for_execution
    assert outcome.trace.recommended_actions[0].sql == outcome.sql_for_execution
    assert "query-join-filter-before" in outcome.trace.rules_triggered
    assert "order_date" in outcome.sql_for_execution


def test_optimizer_reverts_rewrite_when_preflight_fails(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(service, "_inspect_clickhouse_tables", lambda **_: _empty_inspection())
    monkeypatch.setattr(service, "_explain_indexes", lambda **_: None)
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])

    original_sql = (
        "SELECT o.id, c.id "
        "FROM analytics.orders AS o "
        "INNER JOIN analytics.customers AS c ON o.customer_id = c.id "
        "WHERE o.order_date >= toDate('2026-04-01')"
    )
    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=original_sql,
        referenced_tables=["analytics.orders", "analytics.customers"],
        semantic_preflight_service=_PreflightServiceStub("fail"),
        context_tables=["analytics.orders", "analytics.customers"],
        metadata_table_columns={
            "analytics.orders": {"id", "customer_id", "order_date"},
            "analytics.customers": {"id"},
        },
        selected_connector_name="clickhouse-main",
    )

    assert outcome.trace.optimizer_used is True
    assert outcome.trace.applied is False
    assert outcome.sql_for_execution == original_sql
    assert "sql_optimizer_rewrite_reverted_preflight" in outcome.trace.warnings


def test_optimizer_uses_workspace_skill_rule_asset_for_citations(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(service, "_inspect_clickhouse_tables", lambda **_: _empty_inspection())
    monkeypatch.setattr(service, "_explain_indexes", lambda **_: None)
    monkeypatch.setattr(
        service,
        "_match_skills",
        lambda **_: [_skill_with_rule("query-join-filter-before")],
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=(
            "SELECT o.id, c.id "
            "FROM analytics.orders AS o "
            "INNER JOIN analytics.customers AS c ON o.customer_id = c.id "
            "WHERE o.order_date >= toDate('2026-04-01')"
        ),
        referenced_tables=["analytics.orders", "analytics.customers"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders", "analytics.customers"],
        metadata_table_columns={
            "analytics.orders": {"id", "customer_id", "order_date"},
            "analytics.customers": {"id"},
        },
        selected_connector_name="clickhouse-main",
    )

    workspace_citation = next(
        citation
        for citation in outcome.trace.citations
        if citation.kind == "workspace_skill_rule"
    )
    assert workspace_citation.reference.endswith("/rules/query-join-filter-before.md")
    assert "workspace_skill_bundle" in outcome.trace.evidence_used


def test_optimizer_treats_clickhouse_inspection_as_best_effort(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.sql.optimization.clickhouse_connect.get_client",
        lambda **_: (_ for _ in ()).throw(RuntimeError("clickhouse down")),
    )
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])

    original_sql = "SELECT o.id FROM analytics.orders AS o"
    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=original_sql,
        referenced_tables=["analytics.orders"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders"],
        metadata_table_columns={"analytics.orders": {"id"}},
        selected_connector_name="clickhouse-main",
    )

    assert outcome.sql_for_execution == original_sql
    assert outcome.trace.optimizer_used is True
    assert outcome.trace.evidence_used == ["workspace_skill_bundle"]


def test_optimizer_skips_live_clickhouse_calls_for_simple_query(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(
        "rhetorica_backend.domain.chat.sql.optimization.clickhouse_connect.get_client",
        lambda **_: (_ for _ in ()).throw(AssertionError("optimizer touched ClickHouse")),
    )
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])

    original_sql = "SELECT o.id FROM analytics.orders AS o"
    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=original_sql,
        referenced_tables=["analytics.orders"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["analytics.orders"],
        metadata_table_columns={"analytics.orders": {"id"}},
        selected_connector_name="clickhouse-main",
    )

    assert outcome.sql_for_execution == original_sql
    assert outcome.trace.optimizer_used is True
    assert outcome.trace.evidence_used == ["workspace_skill_bundle"]


def test_optimizer_scopes_skip_index_finding_to_filtered_alias(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])
    monkeypatch.setattr(service, "_explain_indexes", lambda **_: "Indexes:")
    monkeypatch.setattr(
        service,
        "_inspect_clickhouse_tables",
        lambda **_: ClickHouseInspectionResult(
            tables=[
                TableOptimizationMetadata(
                    fqn="dm.ft_order",
                    database="dm",
                    table="ft_order",
                    engine="MergeTree",
                    ddl="CREATE TABLE dm.ft_order (...)",
                    sorting_key=["ecom_order_pk"],
                    primary_key=["ecom_order_pk"],
                    partition_key=None,
                    skip_indexes=["idx_order_date"],
                    columns={
                        "ecom_order_pk": "UInt64",
                        "order_create_dttm": "DateTime64",
                    },
                ),
                TableOptimizationMetadata(
                    fqn="dm.ft_order_art",
                    database="dm",
                    table="ft_order_art",
                    engine="MergeTree",
                    ddl="CREATE TABLE dm.ft_order_art (...)",
                    sorting_key=["ecom_order_pk"],
                    primary_key=["ecom_order_pk"],
                    partition_key=None,
                    skip_indexes=[],
                    columns={
                        "ecom_order_pk": "UInt64",
                        "order_create_dttm": "DateTime64",
                        "art_code": "UInt64",
                        "sale_qty": "UInt64",
                    },
                ),
            ],
            evidence_used=["ddl", "system.tables", "system.columns"],
        ),
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=(
            "SELECT oa.art_code, SUM(oa.sale_qty) AS total_qty "
            "FROM dm.ft_order AS o "
            "JOIN dm.ft_order_art AS oa ON o.ecom_order_pk = oa.ecom_order_pk "
            "WHERE toDate(oa.order_create_dttm) = '2026-04-01' "
            "GROUP BY oa.art_code "
            "ORDER BY total_qty DESC "
            "LIMIT 1"
        ),
        referenced_tables=["dm.ft_order", "dm.ft_order_art"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["dm.ft_order", "dm.ft_order_art"],
        metadata_table_columns={
            "dm.ft_order": {"ecom_order_pk", "order_create_dttm"},
            "dm.ft_order_art": {
                "ecom_order_pk",
                "order_create_dttm",
                "art_code",
                "sale_qty",
            },
        },
        selected_connector_name="clickhouse-main",
    )

    skip_index_finding = next(
        finding
        for finding in outcome.trace.findings
        if finding.rule_id == "query-index-skipping-indices"
    )
    assert skip_index_finding.applies_to == ["dm.ft_order_art"]


def test_optimizer_flags_mirrored_date_filter_candidate(monkeypatch) -> None:
    service = SqlOptimizationService()
    monkeypatch.setattr(service, "_match_skills", lambda **_: [_skill_with_rule("query-join-filter-before")])
    monkeypatch.setattr(service, "_explain_indexes", lambda **_: None)
    monkeypatch.setattr(
        service,
        "_inspect_clickhouse_tables",
        lambda **_: ClickHouseInspectionResult(
            tables=[
                TableOptimizationMetadata(
                    fqn="dm.ft_order",
                    database="dm",
                    table="ft_order",
                    engine="MergeTree",
                    ddl="CREATE TABLE dm.ft_order (...)",
                    sorting_key=["order_create_dttm", "ecom_order_pk"],
                    primary_key=["order_create_dttm", "ecom_order_pk"],
                    partition_key=None,
                    skip_indexes=["idx_order_date"],
                    columns={
                        "ecom_order_pk": "UInt64",
                        "order_create_dttm": "DateTime64",
                    },
                ),
                TableOptimizationMetadata(
                    fqn="dm.ft_order_art",
                    database="dm",
                    table="ft_order_art",
                    engine="MergeTree",
                    ddl="CREATE TABLE dm.ft_order_art (...)",
                    sorting_key=["order_create_dttm", "ecom_order_pk"],
                    primary_key=["order_create_dttm", "ecom_order_pk"],
                    partition_key=None,
                    skip_indexes=["idx_order_date"],
                    columns={
                        "ecom_order_pk": "UInt64",
                        "order_create_dttm": "DateTime64",
                        "art_code": "UInt64",
                        "sale_qty": "UInt64",
                    },
                ),
            ],
            evidence_used=["ddl", "system.tables", "system.columns"],
        ),
    )

    outcome = service.optimize(
        workspace_id="default",
        connection=_connection(),
        sql=(
            "SELECT oa.art_code, SUM(oa.sale_qty) AS total_qty "
            "FROM dm.ft_order AS o "
            "JOIN dm.ft_order_art AS oa ON o.ecom_order_pk = oa.ecom_order_pk "
            "WHERE toDate(oa.order_create_dttm) = '2026-04-01' "
            "GROUP BY oa.art_code "
            "ORDER BY total_qty DESC "
            "LIMIT 1"
        ),
        referenced_tables=["dm.ft_order", "dm.ft_order_art"],
        semantic_preflight_service=_PreflightServiceStub("pass"),
        context_tables=["dm.ft_order", "dm.ft_order_art"],
        metadata_table_columns={
            "dm.ft_order": {"ecom_order_pk", "order_create_dttm"},
            "dm.ft_order_art": {
                "ecom_order_pk",
                "order_create_dttm",
                "art_code",
                "sale_qty",
            },
        },
        selected_connector_name="clickhouse-main",
    )

    mirrored_filter_finding = next(
        finding
        for finding in outcome.trace.findings
        if finding.rule_id == "query-join-filter-before"
        and "mirrored before the JOIN" in finding.title
    )
    assert mirrored_filter_finding.applies_to == ["dm.ft_order", "dm.ft_order_art"]
    assert "o.order_create_dttm" in mirrored_filter_finding.summary
