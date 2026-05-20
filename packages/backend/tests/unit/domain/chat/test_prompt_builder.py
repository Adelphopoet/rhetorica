"""Tests for PromptBuilder."""

from datetime import datetime, timedelta, timezone

from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.metadata.metadata_service import TableContext


class TestPromptBuilder:
    """Tests for PromptBuilder."""

    def test_build_system_prompt_includes_current_datetime_context(self):
        """Test system prompt includes deterministic current time context."""
        current_datetime = datetime(
            2026,
            5,
            10,
            14,
            30,
            tzinfo=timezone(timedelta(hours=3), name="MSK"),
        )

        prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=True,
            metadata_configured=True,
            has_db_connectors=True,
            current_datetime=current_datetime,
        )

        assert "Current date/time context:" in prompt
        assert "current_datetime=2026-05-10T14:30:00+03:00" in prompt
        assert "current_weekday=Sunday" in prompt
        assert "current_timezone=MSK" in prompt
        assert "Resolve relative dates like today, yesterday, this week, and this month" in prompt

    def test_build_finalizer_system_prompt_does_not_repeat_current_datetime_context(self):
        prompt = PromptBuilder.build_finalizer_system_prompt()

        assert "Current date/time context:" not in prompt
        assert "current_datetime=" not in prompt

    def test_build_system_prompt_with_all_features(self):
        """Test system prompt with all features enabled."""
        prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=True,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=True,
            confluence_configured=True,
            jira_read_configured=True,
            jira_write_configured=True,
        )

        assert "metadata_tool_available=true" in prompt
        assert "rag_tool_available=true" in prompt
        assert "confluence_tool_available=true" in prompt
        assert "jira_read_tool_available=true" in prompt
        assert "jira_write_tool_available=true" in prompt
        assert "run_sql_tool_available=true" in prompt
        assert "prompt_version=" in prompt
        assert "fetch_metadata_tables" in prompt
        assert "run_sql" in prompt
        assert "build_visualization" in prompt
        assert "validated VisualizationSpec v2 in final visualization" in prompt
        assert "metric-by-date SQL results are visual answers" in prompt
        assert "Prefer governed tables with certification" in prompt
        assert "glossary terms and metrics" in prompt
        assert (
            "For short keyword lookup like `price`, `orders`, or `dau`, prefer table-first metadata lookup"
            in prompt
        )
        assert "Do not concatenate translations or loose synonyms" in prompt
        assert (
            "OpenMetadata search syntax: use `*term*` for substring, uppercase `AND/OR/NOT` for boolean logic, and `field:value` for explicit field filters"
            in prompt
        )
        assert (
            "Prefer business-layer marts like dm over technical layers like stg/ods/dds" in prompt
        )
        assert "prefer canonical tables over source-specific variants" in prompt.lower()
        assert (
            "Treat sandbox/test/tmp/dev/mock/sample/playground objects as suspicious defaults"
            in prompt
        )
        assert "Do not pretend one garbage table is the right answer" in prompt
        assert "do not answer from a broad candidate list alone" in prompt.lower()
        assert "Do not answer a column-existence question from a partial column preview" in prompt
        assert "ground the target table and exact columns before the first run_sql" in prompt
        assert "Never invent near-match names like created vs create" in prompt
        assert "what a table or entity is" in prompt
        assert (
            "Explicit SQL inspection commands like DESCRIBE, DESC, SHOW CREATE, EXISTS, and EXPLAIN are execution requests"
            in prompt
        )
        assert "Generate Trino SQL for Trino connectors" in prompt
        assert "never use ClickHouse-only syntax like system.tables" in prompt
        assert (
            "For Trino schema inspection, prefer SHOW COLUMNS, DESCRIBE, or information_schema.columns"
            in prompt
        )
        assert "Do not use SHOW CREATE TABLE on Trino connectors" in prompt
        assert "Never use metadata service prefixes like service.catalog.schema.table" in prompt
        assert "Complex analytical SQL may use JOIN, CTE, and subqueries when needed" in prompt
        assert "Never join tables from different metadata databases" in prompt
        assert "If run_sql returns optimizer_summary or optimizer_hints" in prompt
        assert "Do not ignore optimizer findings" in prompt
        assert "create_jira_issue is a write tool" in prompt
        assert "profile_tools_available=false" in prompt

    def test_build_finalizer_prompts_require_markdown_preservation(self):
        finalizer_prompt = PromptBuilder.build_finalizer_system_prompt()
        repair_prompt = PromptBuilder.build_finalizer_repair_system_prompt()

        assert "Preserve the markdown structure and line breaks" in finalizer_prompt
        assert "Do not collapse tables, lists, headings, blockquotes, or code fences" in (
            finalizer_prompt
        )
        assert "Never render charts as ASCII art, Unicode bar blocks, or fenced text diagrams" in (
            finalizer_prompt
        )
        assert "Visualization is model-owned" in finalizer_prompt
        assert "answer_markdown textual" in finalizer_prompt
        assert "implicit chart construction" in finalizer_prompt
        assert "Example of a valid visualization field" not in finalizer_prompt
        assert "REPLACE_WITH_EXECUTION_ID" not in finalizer_prompt
        assert "metadata materially influenced source selection" in finalizer_prompt
        assert "weak or conflicting match" in finalizer_prompt
        assert "Preserve the markdown structure and line breaks" in repair_prompt
        assert "Never keep or recreate ASCII art, Unicode bar blocks, or fenced text charts" in (
            repair_prompt
        )

    def test_build_system_prompt_without_metadata(self):
        """Test system prompt without metadata."""
        prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=True,
            metadata_configured=False,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
        )

        assert "metadata_tool_available=false" in prompt
        assert "rag_tool_available=false" in prompt
        assert "confluence_tool_available=false" in prompt
        assert "jira_read_tool_available=false" in prompt
        assert "jira_write_tool_available=false" in prompt
        assert "run_sql_tool_available=true" in prompt

    def test_build_system_prompt_without_sql(self):
        """Test system prompt without SQL execution."""
        prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=False,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
            jira_read_configured=False,
            jira_write_configured=False,
        )

        assert "metadata_tool_available=true" in prompt
        assert "run_sql_tool_available=false" in prompt

    def test_build_tools_with_all_features(self):
        """Test tools building with all features enabled."""
        tools = PromptBuilder.build_tools(
            run_sql_allowed=True,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=True,
            confluence_configured=True,
            jira_read_configured=True,
            jira_write_configured=True,
        )

        tool_names = [tool["function"]["name"] for tool in tools]
        assert "fetch_metadata_tables" in tool_names
        assert "search_metadata_catalog" in tool_names
        assert "get_metadata_entity_details" in tool_names
        assert "get_metadata_entity_lineage" in tool_names
        assert "run_sql" in tool_names
        assert "build_visualization" in tool_names
        assert "search_rag_insights" in tool_names
        assert "search_confluence_pages" in tool_names
        assert "search_jira_issues" in tool_names
        assert "get_jira_issue" in tool_names
        assert "create_jira_issue" in tool_names
        assert len(tools) == 11

    def test_build_tools_without_metadata(self):
        """Test tools building without metadata."""
        tools = PromptBuilder.build_tools(
            run_sql_allowed=True,
            metadata_configured=False,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
        )

        tool_names = [tool["function"]["name"] for tool in tools]
        assert "fetch_metadata_tables" not in tool_names
        assert "run_sql" in tool_names
        assert "build_visualization" in tool_names
        assert len(tools) == 2
        run_sql = tools[0]["function"]["parameters"]["properties"]
        assert run_sql["prompt_row_limit"]["maximum"] == 500
        assert "bounded row windows" in tools[0]["function"]["description"]
        build_visualization = tools[1]["function"]
        assert build_visualization["name"] == "build_visualization"
        assert "does not infer chart intent" in build_visualization["description"]

    def test_build_tools_without_sql(self):
        """Test tools building without SQL execution."""
        tools = PromptBuilder.build_tools(
            run_sql_allowed=False,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
        )

        tool_names = [tool["function"]["name"] for tool in tools]
        assert "fetch_metadata_tables" in tool_names
        assert "run_sql" not in tool_names
        assert "build_visualization" not in tool_names
        assert len(tools) == 4

        descriptions = {tool["function"]["name"]: tool["function"]["description"] for tool in tools}
        connector_descriptions = {
            tool["function"]["name"]: tool["function"]["parameters"]["properties"][
                "connector_name"
            ]["description"]
            for tool in tools
            if "connector_name" in tool["function"]["parameters"]["properties"]
        }
        assert (
            "then call get_metadata_entity_details before answering"
            in descriptions["fetch_metadata_tables"]
        )
        assert "Metadata connector name only" in connector_descriptions["fetch_metadata_tables"]
        assert "Metadata connector name only" in connector_descriptions["search_metadata_catalog"]
        limit_schemas = {
            tool["function"]["name"]: tool["function"]["parameters"]["properties"]["limit"]
            for tool in tools
            if "limit" in tool["function"]["parameters"]["properties"]
        }
        assert limit_schemas["fetch_metadata_tables"]["default"] == 20
        assert limit_schemas["search_metadata_catalog"]["default"] == 20
        assert "exact column-existence" in descriptions["get_metadata_entity_details"]

    def test_build_tools_no_connectors(self):
        """Test tools building with no database connectors."""
        tools = PromptBuilder.build_tools(
            run_sql_allowed=True,
            metadata_configured=True,
            has_db_connectors=False,
            rag_configured=False,
            confluence_configured=False,
        )

        tool_names = [tool["function"]["name"] for tool in tools]
        assert "run_sql" not in tool_names

    def test_build_tools_run_sql_connector_name_is_database_connector(self):
        """run_sql should advertise database connector semantics explicitly."""
        tools = PromptBuilder.build_tools(
            run_sql_allowed=True,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
        )

        run_sql_tool = next(tool for tool in tools if tool["function"]["name"] == "run_sql")
        description = run_sql_tool["function"]["parameters"]["properties"]["connector_name"][
            "description"
        ]
        properties = run_sql_tool["function"]["parameters"]["properties"]

        assert "Database connector name only" in description
        assert properties["requires_approval"]["type"] == "boolean"
        assert "approval_token" in properties

    def test_build_tools_includes_profile_tools_when_enabled(self):
        tools = PromptBuilder.build_tools(
            run_sql_allowed=False,
            metadata_configured=False,
            has_db_connectors=True,
            profile_tools_allowed=True,
        )

        tool_names = [tool["function"]["name"] for tool in tools]
        assert tool_names == ["profile_table", "profile_metric"]
        profile_table = tools[0]["function"]
        profile_metric = tools[1]["function"]
        assert "bounded table profile" in profile_table["description"]
        assert "metric_name" in profile_metric["parameters"]["required"]

    def test_discovery_user_message_includes_database_context_for_profile_tools(self):
        db_connectors = [
            type(
                "ConnectorItem",
                (),
                {
                    "name": "test-db",
                    "connection": type(
                        "Connection",
                        (),
                        {"engine": "postgresql", "extra_settings": {}},
                    ),
                },
            )()
        ]

        message = PromptBuilder.build_user_message(
            question="Профиль таблицы dm.orders",
            db_connectors=db_connectors,
            metadata_connector_names=[],
            prompt_mode="discovery",
            profile_tools_allowed=True,
        )

        assert "Database connectors:" in message
        assert "name=test-db" in message

    def test_build_system_prompt_discovery_mode_omits_sql_only_rules(self):
        """Discovery mode should stay focused on lookup instead of SQL repair."""
        prompt = PromptBuilder.build_system_prompt(
            run_sql_allowed=False,
            metadata_configured=True,
            has_db_connectors=True,
            rag_configured=False,
            confluence_configured=False,
            prompt_mode="discovery",
        )

        assert "fetch_metadata_tables" in prompt
        assert "If run_sql returns sql_preflight_failed" not in prompt

    def test_build_user_message_discovery_mode_trims_database_context(self):
        """Discovery mode should not stuff database connector payloads into user message."""
        db_connectors = [
            type(
                "ConnectorItem",
                (),
                {
                    "name": "test-db",
                    "connection": type(
                        "Connection",
                        (),
                        {"engine": "postgresql", "extra_settings": {}},
                    ),
                },
            )()
        ]

        message = PromptBuilder.build_user_message(
            question="Найди таблицу с заказами",
            db_connectors=db_connectors,
            metadata_connector_names=["openmetadata"],
            prompt_mode="discovery",
        )

        assert "Metadata connectors:" in message
        assert "Database connectors:" not in message

    def test_build_user_message(self):
        """Test user message building."""
        db_connectors = [
            type(
                "ConnectorItem",
                (),
                {
                    "name": "test-db",
                    "connection": type(
                        "Connection",
                        (),
                        {"engine": "postgresql", "extra_settings": {}},
                    ),
                },
            )()
        ]
        metadata_connectors = ["openmetadata"]

        message = PromptBuilder.build_user_message(
            question="What tables are available?",
            db_connectors=db_connectors,
            metadata_connector_names=metadata_connectors,
            rag_enabled=True,
            confluence_connector_names=["confluence-main"],
            confluence_enabled=True,
            jira_connector_names=["jira-main"],
            jira_enabled=True,
            jira_create_enabled=True,
            recent_tables=["analytics.metrics.daily"],
            grounded_tables=[
                TableContext(
                    source="openmetadata",
                    fqn="analytics.metrics.daily",
                    columns=["day", "gmv", "orders_count"],
                )
            ],
        )

        assert "Question: What tables are available?" in message
        assert "name=test-db" in message
        assert "engine=postgresql" in message
        assert "- openmetadata" in message
        assert "RAG enabled: true" in message
        assert "- confluence-main" in message
        assert "- jira-main" in message
        assert "Jira create enabled: true" in message
        assert "Confluence enabled: true" in message
        assert "Recent grounded tables:" in message
        assert "- analytics.metrics.daily" in message
        assert "Grounded schema hints:" in message
        assert "columns=day, gmv, orders_count" in message
        assert "certification=Certification.Gold" in PromptBuilder._grounded_table_lines([
            TableContext(
                source="openmetadata",
                fqn="analytics.metrics.daily",
                columns=["day", "gmv", "orders_count"],
                certification="Certification.Gold",
                tier="Tier.Tier1",
                owners=["analytics"],
                domains=["sales"],
                matched_on=["name_exact", "business_schema"],
            )
        ])

    def test_build_user_message_includes_semantic_hints_for_grounded_tables(self):
        message = PromptBuilder.build_user_message(
            question="What tables are available?",
            db_connectors=[],
            metadata_connector_names=["openmetadata"],
            grounded_tables=[
                TableContext(
                    source="openmetadata",
                    fqn="Sample-ClickHouse.analytics.dm.sales_orders",
                    columns=["day", "gmv"],
                    schema="dm",
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    matched_on=["name_exact", "business_schema"],
                )
            ],
        )

        assert "certification=Certification.Gold" in message
        assert "tier=Tier.Tier1" in message
        assert "owners=analytics" in message
        assert "domains=sales" in message
        assert "matched_on=name_exact, business_schema" in message
        assert "suspicious=no" in message

    def test_build_user_message_includes_executable_sql_reference_for_trino_grounding(self):
        db_connectors = [
            type(
                "ConnectorItem",
                (),
                {
                    "name": "trino-adhoc",
                    "connection": type(
                        "Connection",
                        (),
                        {
                            "engine": "trino",
                            "extra_settings": {
                                "metadata_service": "DWH-iceberrg",
                                "metadata_database": "iceberg",
                                "metadata_schema": "dm_ft",
                            },
                        },
                    )(),
                },
            )()
        ]

        message = PromptBuilder.build_user_message(
            question="join ft_order and ft_order_art",
            db_connectors=db_connectors,
            metadata_connector_names=["openmetadata"],
            recent_tables=["DWH-iceberrg.iceberg.dm_ft.ft_order"],
            grounded_tables=[
                TableContext(
                    source="openmetadata",
                    fqn="DWH-iceberrg.iceberg.dm_ft.ft_order_art",
                    columns=["ecom_order_pk", "order_source"],
                    schema="dm_ft",
                )
            ],
        )

        assert "DWH-iceberrg.iceberg.dm_ft.ft_order (sql: iceberg.dm_ft.ft_order)" in message
        assert "sql_ref=iceberg.dm_ft.ft_order_art" in message

    def test_build_user_message_no_connectors(self):
        """Test user message building with no connectors."""
        message = PromptBuilder.build_user_message(
            question="Hello",
            db_connectors=[],
            metadata_connector_names=[],
            rag_enabled=False,
            confluence_connector_names=[],
            confluence_enabled=False,
        )

        assert "- none" in message

    def test_mapping_signature(self):
        """Test mapping signature building."""
        connector = type(
            "ConnectorItem",
            (),
            {
                "connection": type(
                    "Connection",
                    (),
                    {
                        "extra_settings": {
                            "metadata_service": "postgres",
                            "metadata_database": "analytics",
                            "metadata_schema": "public",
                        }
                    },
                )
            },
        )()

        signature = PromptBuilder._mapping_signature(connector)
        assert signature == "postgres.analytics.public"

    def test_mapping_signature_defaults(self):
        """Test mapping signature with defaults."""
        connector = type(
            "ConnectorItem",
            (),
            {"connection": type("Connection", (), {"extra_settings": {}})()},
        )()

        signature = PromptBuilder._mapping_signature(connector)
        assert signature == "*.*.*"

    def test_sql_scope_signature_for_trino(self):
        """Test executable SQL scope hint for Trino."""
        connector = type(
            "ConnectorItem",
            (),
            {
                "connection": type(
                    "Connection",
                    (),
                    {"engine": "trino", "extra_settings": {}},
                )()
            },
        )()

        assert PromptBuilder._sql_scope_signature(connector) == "any_catalog.any_schema.table"
