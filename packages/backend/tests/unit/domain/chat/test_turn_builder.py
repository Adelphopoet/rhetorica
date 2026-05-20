from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillItem,
)
from rhetorica_backend.domain.chat.orchestration.turn_builder import (
    TurnBuilder,
    WorkspaceToolingSnapshot,
)


class _SqlRouterStub:
    @staticmethod
    def is_non_analytic_question(_question: str) -> bool:
        return False


class _WorkspaceSkillRepositoryStub:
    def __init__(self, skills: list[WorkspaceSkillItem]) -> None:
        self._skills = skills

    def list_active_for_capability(self, **_kwargs) -> list[WorkspaceSkillItem]:
        return list(self._skills)


class _NoPreLlmMetadataRepositoryStub:
    def get_connection(self, **_kwargs):
        raise AssertionError("metadata connection must not be resolved before LLM tool call")


class _NoPreLlmMetadataTableLoaderStub:
    def fetch_tables(self, **_kwargs):
        raise AssertionError("metadata tables must not be fetched before LLM tool call")


class _NoPreLlmMetadataCatalogSearchStub:
    def search_catalog(self, **_kwargs):
        raise AssertionError("metadata catalog must not be searched before LLM tool call")


class _NoPreLlmMetadataRankingStub:
    def filter_tables(self, **_kwargs):
        raise AssertionError("metadata tables must not be ranked before LLM tool call")


class _NoPreLlmMetadataSemanticGroundingStub:
    def build_semantic_grounding_payload(self, *_args, **_kwargs):
        raise AssertionError("semantic grounding must not be built before LLM tool call")


def _connector_item(
    *,
    name: str,
    engine: str,
    metadata_service: str,
    metadata_database: str,
    metadata_schema: str,
) -> ConnectorItem:
    now = datetime.now(tz=UTC)
    return ConnectorItem(
        id=f"{name}-id",
        name=name,
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine=engine,
            host="localhost",
            port=8123 if engine == "clickhouse" else 443,
            username="tester",
            database=metadata_database if engine == "clickhouse" else None,
            catalog=metadata_database if engine == "trino" else None,
            schema=metadata_schema or None,
            extra_settings={
                "metadata_service": metadata_service,
                "metadata_database": metadata_database,
                "metadata_schema": metadata_schema,
            },
        ),
        has_password=False,
        created_at=now,
        updated_at=now,
    )


def test_prepare_turn_does_not_auto_fetch_metadata_before_llm_tool_call() -> None:
    builder = TurnBuilder(
        openmetadata_connector_repository=_NoPreLlmMetadataRepositoryStub(),
        metadata_fetcher=None,
        metadata_table_loader=_NoPreLlmMetadataTableLoaderStub(),
        metadata_catalog_search_service=_NoPreLlmMetadataCatalogSearchStub(),
        metadata_ranking_service=_NoPreLlmMetadataRankingStub(),
        metadata_semantic_grounding_service=_NoPreLlmMetadataSemanticGroundingStub(),
        sql_router=_SqlRouterStub(),
    )

    prepared = builder.prepare_turn(
        request=ChatAskRequest(
            question="Покажи GMV по дням",
            workspace_id="default",
            run_sql=True,
        ),
        db_connectors=[
            _connector_item(
                name="clickhouse-main",
                engine="clickhouse",
                metadata_service="Sample-ClickHouse",
                metadata_database="default",
                metadata_schema="dm",
            )
        ],
        conversation_history=[],
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=True,
            metadata_configured=True,
            metadata_connector_names=["openmetadata"],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
        ),
    )

    assert prepared.bootstrap_result.tables == []
    assert prepared.state.context_tables == []
    assert prepared.state.metadata_table_columns == {}
    assert "fetch_metadata_tables" in prepared.state.capability_state.available_tools
    assert "search_metadata_catalog" in prepared.state.capability_state.available_tools
    assert "Grounded schema hints:\n- none" in prepared.messages[-1]["content"]


def test_prepare_turn_restores_metadata_columns_from_runtime_digest() -> None:
    builder = TurnBuilder(
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=_SqlRouterStub(),
    )

    prepared = builder.prepare_turn(
        request=ChatAskRequest(
            question="а теперь исключи этого юзера",
            workspace_id="default",
        ),
        db_connectors=[],
        conversation_history=[
            {
                "role": "assistant",
                "content": (
                    "Context digest:\n"
                    "selected_connector=clickhouse-main\n"
                    "recent_tables=Sample-ClickHouse.default.dm.ft_order, "
                    "Sample-ClickHouse.default.dm.ft_order_unit_economics_v\n"
                    "table_columns.Sample-ClickHouse.default.dm.ft_order=order_id|customer_id|ecom_order_pk\n"
                    "table_columns.Sample-ClickHouse.default.dm.ft_order_unit_economics_v="
                    "ecom_order_pk|cm2_amt|create_date\n"
                ),
            }
        ],
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=False,
            metadata_configured=False,
            metadata_connector_names=[],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
        ),
    )

    assert prepared.state.context_tables == [
        "Sample-ClickHouse.default.dm.ft_order",
        "Sample-ClickHouse.default.dm.ft_order_unit_economics_v",
    ]
    assert prepared.state.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_order": {
            "order_id",
            "customer_id",
            "ecom_order_pk",
        },
        "sample-clickhouse.default.dm.ft_order_unit_economics_v": {
            "ecom_order_pk",
            "cm2_amt",
            "create_date",
        },
    }
    assert prepared.state.selected_database_connector == "clickhouse-main"


def test_prepare_turn_injects_workspace_skill_guidance_for_sql_mode() -> None:
    now = datetime.now(tz=UTC)
    skill = WorkspaceSkillItem(
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
                    path="skills/clickhouse-best-practices/SKILL.md",
                    source_url=(
                        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
                        "skills/clickhouse-best-practices/SKILL.md"
                    ),
                    kind="main",
                    title="ClickHouse Best Practices",
                    summary="Official ClickHouse skill.",
                    content_markdown="# ClickHouse Best Practices\n\nOfficial ClickHouse skill.\n",
                ),
                WorkspaceSkillAsset(
                    path=(
                        "skills/clickhouse-best-practices/"
                        "rules/query-join-filter-before.md"
                    ),
                    source_url=(
                        "https://raw.githubusercontent.com/ClickHouse/agent-skills/main/"
                        "skills/clickhouse-best-practices/rules/query-join-filter-before.md"
                    ),
                    kind="rule",
                    title="Filter Tables Before Joining",
                    summary="Joining full tables then filtering wastes resources.",
                    rule_id="query-join-filter-before",
                    impact="CRITICAL",
                    tags=["query", "join"],
                    content_markdown="## Filter Tables Before Joining\n\nJoining full tables then filtering wastes resources.\n",
                ),
            ]
        ),
        created_at=now,
        updated_at=now,
    )
    builder = TurnBuilder(
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=_SqlRouterStub(),
        workspace_skill_repository=_WorkspaceSkillRepositoryStub([skill]),
        tool_executor_factory=lambda **_kwargs: object(),
    )

    prepared = builder.prepare_turn(
        request=ChatAskRequest(
            question="Покажи GMV по дням",
            workspace_id="default",
        ),
        db_connectors=[
            type(
                "ConnectorItem",
                (),
                {
                    "name": "clickhouse-main",
                    "connection": type(
                        "Connection",
                        (),
                        {"engine": "clickhouse", "extra_settings": {}},
                    )(),
                },
            )()
        ],
        conversation_history=[],
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=True,
            metadata_configured=False,
            metadata_connector_names=[],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
        ),
    )

    system_prompt = prepared.messages[0]["content"]
    assert "Workspace skill guidance:" in system_prompt
    assert "query-join-filter-before" in system_prompt
    assert "Filter Tables Before Joining" in system_prompt
    assert "workspace_skill_prompt" in prepared.state.capability_state.used_providers


def test_prepare_turn_explicit_connector_request_overrides_runtime_connector() -> None:
    builder = TurnBuilder(
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=_SqlRouterStub(),
    )

    clickhouse = _connector_item(
        name="clickhouse",
        engine="clickhouse",
        metadata_service="Sample-ClickHouse",
        metadata_database="default",
        metadata_schema="dm",
    )
    trino = _connector_item(
        name="trino-adhoc",
        engine="trino",
        metadata_service="trino_omni",
        metadata_database="iceberg",
        metadata_schema="dm_ft",
    )

    prepared = builder.prepare_turn(
        request=ChatAskRequest(
            question="а теперь из clickhouse пример строк из такой же таблицы",
            workspace_id="default",
        ),
        db_connectors=[clickhouse, trino],
        conversation_history=[
            {
                "role": "assistant",
                "content": (
                    "Context digest:\n"
                    "selected_connector=trino-adhoc\n"
                    "recent_tables=trino_omni.iceberg.dm_ft.ft_order\n"
                    "table_columns.trino_omni.iceberg.dm_ft.ft_order=order_id|order_source\n"
                ),
            }
        ],
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=True,
            metadata_configured=False,
            metadata_connector_names=[],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
        ),
    )

    assert prepared.state.selected_database_connector == "clickhouse"
    assert prepared.state.execution_zone is not None
    assert prepared.state.execution_zone.connector_name == "clickhouse"
    assert prepared.state.context_tables == []
    assert prepared.state.metadata_table_columns == {}


def test_prepare_turn_restores_active_analysis_checkpoint_from_state() -> None:
    builder = TurnBuilder(
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=_SqlRouterStub(),
    )

    prepared = builder.prepare_turn(
        request=ChatAskRequest(
            question="Продолжи анализ с текущего чекпоинта",
            workspace_id="default",
        ),
        db_connectors=[],
        conversation_history=[],
        conversation_state={
            "analysis_checkpoint": {
                "run_id": "run-1",
                "plan_id": "plan-1",
                "status": "running",
                "completed_step_ids": ["baseline"],
                "evidence_ids": ["exec-1"],
            },
            "analysis_plan": {
                "plan_id": "plan-1",
                "goal": "Почему упала выручка?",
                "status": "running",
                "steps": [
                    {
                        "id": "baseline",
                        "title": "Build baseline",
                        "status": "done",
                        "required_evidence": ["execution_result"],
                        "evidence_ids": ["execution_result", "exec-1"],
                    },
                    {
                        "id": "breakdown",
                        "title": "Breakdown",
                        "status": "planned",
                    },
                ],
                "required_evidence": ["execution_result"],
            },
        },
        workspace_snapshot=WorkspaceToolingSnapshot(
            has_db_connectors=False,
            metadata_configured=False,
            metadata_connector_names=[],
            rag_configured=False,
            confluence_configured=False,
            confluence_connector_names=[],
        ),
    )

    assert prepared.state.checkpoint is not None
    assert prepared.state.checkpoint.run_id == "run-1"
    assert prepared.state.analysis_plan is not None
    assert prepared.state.analysis_plan.plan_id == "plan-1"
    assert "analysis_checkpoint_restored" in prepared.state.warnings
