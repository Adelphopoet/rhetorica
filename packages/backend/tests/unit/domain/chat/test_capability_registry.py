"""Tests for tool-first capability registry and trace helpers."""

from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    build_workspace_capability_surface,
    capability_trace_from_runtime,
    resolve_available_tool_names,
)
from rhetorica_backend.domain.chat.orchestration.turn_policy import TurnPolicy


def test_workspace_capability_surface_marks_degraded_semantic_path_without_metadata() -> None:
    surface = build_workspace_capability_surface(
        has_db_connectors=True,
        metadata_configured=False,
        rag_configured=True,
        confluence_configured=False,
        jira_configured=False,
        policy=TurnPolicy(
            prompt_mode="sql_generation",
            metadata_tools_enabled=False,
            rag_tools_enabled=True,
            confluence_tools_enabled=False,
            run_sql_tools_enabled=True,
            profile_tools_enabled=False,
            is_non_analytic_question=False,
            requires_metadata_lookup=False,
        ),
        run_sql_requested=True,
    )

    assert surface.available_tools == ["run_sql", "build_visualization", "search_rag_insights"]
    assert "semantic_discovery_preferred_provider_missing" in surface.degraded_capabilities
    assert "sql_grounding_degraded_without_governed_metadata" in surface.degraded_capabilities
    assert "prompt_mode:sql_generation" in surface.policy_decisions


def test_capability_trace_includes_runtime_fallback_and_evidence_origin() -> None:
    trace = capability_trace_from_runtime(
        available_tools=["fetch_metadata_tables", "run_sql"],
        used_tools=["fetch_metadata_tables", "run_sql"],
        used_providers=["openmetadata:om-prod", "database:clickhouse-main"],
        metadata_sources=["runtime", "confluence"],
        warnings=["metadata_runtime_lookup_used"],
        has_execution=True,
        degraded_capabilities=["semantic_discovery_preferred_provider_missing"],
        policy_decisions=["prompt_mode:sql_generation"],
    )

    assert trace.available_tools == ["fetch_metadata_tables", "run_sql"]
    assert trace.used_tools == ["fetch_metadata_tables", "run_sql"]
    assert trace.used_providers == ["openmetadata:om-prod", "database:clickhouse-main"]
    assert trace.evidence_origin == [
        "live_sql",
        "internal_docs",
        "database_runtime_introspection",
    ]
    assert "semantic_discovery_preferred_provider_missing" in trace.degraded_capabilities
    assert "semantic_discovery_fallback_database_runtime" in trace.degraded_capabilities


def test_capability_surface_matches_prompt_tool_definitions() -> None:
    policy = TurnPolicy(
        prompt_mode="sql_generation",
        metadata_tools_enabled=True,
        rag_tools_enabled=True,
        confluence_tools_enabled=True,
        jira_read_tools_enabled=True,
        jira_write_tools_enabled=True,
        run_sql_tools_enabled=True,
        profile_tools_enabled=True,
        is_non_analytic_question=False,
        requires_metadata_lookup=False,
    )

    available_names = resolve_available_tool_names(policy)
    prompt_names = [
        tool["function"]["name"]
        for tool in PromptBuilder.build_tools(
            run_sql_allowed=policy.run_sql_tools_enabled,
            metadata_configured=policy.metadata_tools_enabled,
            has_db_connectors=True,
            rag_configured=policy.rag_tools_enabled,
            confluence_configured=policy.confluence_tools_enabled,
            jira_read_configured=policy.jira_read_tools_enabled,
            jira_write_configured=policy.jira_write_tools_enabled,
            profile_tools_allowed=policy.profile_tools_enabled,
        )
    ]

    assert prompt_names == available_names
