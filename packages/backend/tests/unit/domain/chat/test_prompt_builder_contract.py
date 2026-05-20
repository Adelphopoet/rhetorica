"""Contract tests for prompt builder instructions."""

from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder


def test_system_prompt_requires_disambiguation_for_prefix_matches() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=True,
        metadata_configured=True,
        has_db_connectors=True,
        rag_configured=False,
        confluence_configured=False,
    )

    assert "multiple matching tables" in prompt
    assert "exact leaf-name match" in prompt


def test_system_prompt_requires_preflight_guided_sql_repair() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=True,
        metadata_configured=True,
        has_db_connectors=True,
        rag_configured=False,
        confluence_configured=False,
    )

    assert "If run_sql returns sql_preflight_failed" in prompt
    assert "If run_sql returns sql_preflight_uncertain" in prompt
    assert "If run_sql returns optimizer_summary or optimizer_hints" in prompt
    assert "Do not ignore optimizer findings" in prompt
    assert "do not repeat the same run_sql call" in prompt
    assert "prompt_row_limit" in prompt
    assert "answer-ready aggregate SQL" in prompt
    assert "maximum is 500" in prompt
    assert "LIMIT/OFFSET" in prompt


def test_system_prompt_requires_connected_connector_execution_only() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=True,
        metadata_configured=True,
        has_db_connectors=True,
        rag_configured=False,
        confluence_configured=False,
    )

    assert "Use run_sql only with connector names from the Database connectors list" in prompt
    assert "Metadata may mention tables from unconnected sources" in prompt


def test_system_prompt_requires_single_execution_zone_for_multi_table_sql() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=True,
        metadata_configured=True,
        has_db_connectors=True,
        rag_configured=False,
        confluence_configured=False,
    )

    assert "Complex analytical SQL may use JOIN, CTE, and subqueries when needed" in prompt
    assert "Before multi-table SQL, ground every table and join key" in prompt
    assert "Never join tables from different metadata databases" in prompt
