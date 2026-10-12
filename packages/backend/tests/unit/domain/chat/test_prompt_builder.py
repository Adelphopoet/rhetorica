from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder


def _tool_names(tools: list[dict]) -> set[str]:
    return {tool["function"]["name"] for tool in tools}


def test_system_prompt_exposes_sdk_managed_tool_contract() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=True,
        has_db_connectors=True,
        mcp_configured=True,
    )

    assert "Current turn mode is sql_generation" in prompt
    assert "native function tools" in prompt
    assert "mcp_tool_available=true" in prompt
    assert "run_sql_tool_available=true" in prompt
    assert "Current date/time context" not in prompt

    time_context = PromptBuilder.build_time_context(
        datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    )
    assert "2026-08-31" in time_context


def test_tool_catalog_contains_only_requested_active_families() -> None:
    tools = PromptBuilder.build_tools(
        run_sql_allowed=True,
        has_db_connectors=True,
        rag_configured=True,
        documentation_configured=True,
    )

    assert _tool_names(tools) == {
        "run_sql",
        "build_visualization",
        "search_rag_insights",
        "search_documentation",
        "read_documentation_page",
    }


def test_user_message_lists_generic_mcp_connectors() -> None:
    connector = SimpleNamespace(
        name="catalog-mcp",
        connection=SimpleNamespace(
            provider="streamable_http",
            tool_name_prefix="catalog",
            enabled_groups="",
        ),
        usage_description="Use for governed catalog lookup",
    )

    message = PromptBuilder.build_user_message(
        question="Find the governed metric",
        db_connectors=[],
        mcp_connector_names=[connector],
        mcp_enabled=True,
        prompt_mode="discovery",
    )

    assert "MCP connectors" in message
    assert "name=catalog-mcp" in message
    assert "tool_prefix=catalog" in message


def test_direct_response_prompt_has_no_tool_protocol() -> None:
    prompt = PromptBuilder.build_system_prompt(
        run_sql_allowed=False,
        has_db_connectors=False,
    )

    assert "Return the final user-facing answer directly" in prompt
    assert "load_tool_group" not in prompt
