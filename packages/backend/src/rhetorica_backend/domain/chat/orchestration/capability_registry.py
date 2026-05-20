"""Tool-first capability registry and trace helpers for chat orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from rhetorica_backend.contracts.chat import CapabilityTrace, RuntimeContextTrace

from .turn_policy import TurnPolicy


@dataclass(frozen=True)
class ToolCapabilitySpec:
    """Registry record for one runtime tool."""

    name: str
    family: str
    primary_provider: str
    fallback_providers: tuple[str, ...] = ()


TOOL_CAPABILITY_REGISTRY: dict[str, ToolCapabilitySpec] = {
    "fetch_metadata_tables": ToolCapabilitySpec(
        name="fetch_metadata_tables",
        family="semantic_discovery",
        primary_provider="openmetadata",
        fallback_providers=("mcp", "database_runtime"),
    ),
    "search_metadata_catalog": ToolCapabilitySpec(
        name="search_metadata_catalog",
        family="semantic_discovery",
        primary_provider="openmetadata",
        fallback_providers=("mcp",),
    ),
    "get_metadata_entity_details": ToolCapabilitySpec(
        name="get_metadata_entity_details",
        family="semantic_discovery",
        primary_provider="openmetadata",
        fallback_providers=("mcp",),
    ),
    "get_metadata_entity_lineage": ToolCapabilitySpec(
        name="get_metadata_entity_lineage",
        family="semantic_discovery",
        primary_provider="openmetadata",
        fallback_providers=("mcp",),
    ),
    "run_sql": ToolCapabilitySpec(
        name="run_sql",
        family="sql_execution",
        primary_provider="database",
        fallback_providers=("database_runtime",),
    ),
    "build_visualization": ToolCapabilitySpec(
        name="build_visualization",
        family="result_visualization",
        primary_provider="backend",
    ),
    "search_rag_insights": ToolCapabilitySpec(
        name="search_rag_insights",
        family="insight_retrieval",
        primary_provider="rag",
    ),
    "search_confluence_pages": ToolCapabilitySpec(
        name="search_confluence_pages",
        family="docs_retrieval",
        primary_provider="confluence",
    ),
    "search_jira_issues": ToolCapabilitySpec(
        name="search_jira_issues",
        family="issue_management",
        primary_provider="jira",
    ),
    "get_jira_issue": ToolCapabilitySpec(
        name="get_jira_issue",
        family="issue_management",
        primary_provider="jira",
    ),
    "create_jira_issue": ToolCapabilitySpec(
        name="create_jira_issue",
        family="issue_management",
        primary_provider="jira",
    ),
    "profile_table": ToolCapabilitySpec(
        name="profile_table",
        family="data_profiling",
        primary_provider="database",
    ),
    "profile_metric": ToolCapabilitySpec(
        name="profile_metric",
        family="data_profiling",
        primary_provider="database",
    ),
}


@dataclass(frozen=True)
class WorkspaceCapabilitySurface:
    """Derived tool surface and degradation state for a workspace turn."""

    available_tools: list[str]
    degraded_capabilities: list[str]
    policy_decisions: list[str]


@dataclass
class RuntimeCapabilityState:
    """Mutable runtime capability state accumulated during a turn."""

    available_tools: list[str] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    used_providers: list[str] = field(default_factory=list)
    degraded_capabilities: list[str] = field(default_factory=list)
    policy_decisions: list[str] = field(default_factory=list)


def build_workspace_capability_surface(
    *,
    has_db_connectors: bool,
    metadata_configured: bool,
    rag_configured: bool,
    confluence_configured: bool,
    jira_configured: bool,
    policy: TurnPolicy,
    run_sql_requested: bool,
) -> WorkspaceCapabilitySurface:
    """Derive available tools, degradation, and policy decisions for a turn."""
    available_tools = resolve_available_tool_names(policy)
    degraded_capabilities: list[str] = []
    policy_decisions = [f"prompt_mode:{policy.prompt_mode}"]

    if not metadata_configured:
        degraded_capabilities.append("semantic_discovery_preferred_provider_missing")
        if has_db_connectors:
            degraded_capabilities.append("sql_grounding_degraded_without_governed_metadata")
    if not confluence_configured:
        degraded_capabilities.append("docs_retrieval_provider_missing")
    if not jira_configured:
        degraded_capabilities.append("issue_management_provider_missing")
    if not rag_configured:
        degraded_capabilities.append("insight_retrieval_provider_missing")
    if not has_db_connectors:
        degraded_capabilities.append("sql_execution_provider_missing")

    if policy.is_non_analytic_question:
        policy_decisions.append("non_analytic_turn_tools_suppressed")
    if not run_sql_requested:
        policy_decisions.append("run_sql_disabled_by_request")
    if policy.requires_metadata_lookup:
        policy_decisions.append("metadata_lookup_required_before_sql")
    if run_sql_requested and has_db_connectors and not policy.run_sql_tools_enabled:
        policy_decisions.append("run_sql_not_available_in_current_turn")
    if not available_tools:
        policy_decisions.append("workspace_effectively_llm_only")
    if policy.jira_write_tools_enabled:
        policy_decisions.append("jira_create_enabled_by_explicit_request")

    return WorkspaceCapabilitySurface(
        available_tools=available_tools,
        degraded_capabilities=_unique_preserve_order(degraded_capabilities),
        policy_decisions=_unique_preserve_order(policy_decisions),
    )


def resolve_available_tool_names(policy: TurnPolicy) -> list[str]:
    """Resolve available tools from turn policy."""
    names: list[str] = []
    if policy.metadata_tools_enabled:
        names.extend([
            "fetch_metadata_tables",
            "search_metadata_catalog",
            "get_metadata_entity_details",
            "get_metadata_entity_lineage",
        ])
    if policy.run_sql_tools_enabled:
        names.append("run_sql")
        names.append("build_visualization")
    if policy.profile_tools_enabled:
        names.extend(["profile_table", "profile_metric"])
    if policy.rag_tools_enabled:
        names.append("search_rag_insights")
    if policy.confluence_tools_enabled:
        names.append("search_confluence_pages")
    if policy.jira_read_tools_enabled:
        names.extend(["search_jira_issues", "get_jira_issue"])
    if policy.jira_write_tools_enabled:
        names.append("create_jira_issue")
    return names


def capability_trace_from_runtime(
    *,
    available_tools: list[str],
    used_tools: list[str],
    used_providers: list[str],
    metadata_sources: list[str],
    warnings: list[str],
    has_execution: bool,
    degraded_capabilities: list[str],
    policy_decisions: list[str],
    runtime_context: RuntimeContextTrace | None = None,
) -> CapabilityTrace:
    """Build response trace from runtime state."""
    evidence_origin = derive_evidence_origin(
        metadata_sources=metadata_sources,
        warnings=warnings,
        has_execution=has_execution,
    )
    runtime_degradation = derive_runtime_degradation(
        metadata_sources=metadata_sources,
        warnings=warnings,
    )
    return CapabilityTrace(
        available_tools=_unique_preserve_order(available_tools),
        used_tools=_unique_preserve_order(used_tools),
        used_providers=_unique_preserve_order(used_providers),
        evidence_origin=evidence_origin,
        degraded_capabilities=_unique_preserve_order(
            [*degraded_capabilities, *runtime_degradation]
        ),
        policy_decisions=_unique_preserve_order(policy_decisions),
        runtime_context=runtime_context,
    )


def derive_evidence_origin(
    *,
    metadata_sources: list[str],
    warnings: list[str],
    has_execution: bool,
) -> list[str]:
    """Translate runtime evidence into stable analyst-facing origin labels."""
    origins: list[str] = []
    if has_execution:
        origins.append("live_sql")
    if any(source in {"openmetadata", "mcp"} for source in metadata_sources):
        origins.append("governed_metadata")
    if "confluence" in metadata_sources:
        origins.append("internal_docs")
    if "rag" in metadata_sources:
        origins.append("rag_insights")
    if "runtime" in metadata_sources or "metadata_runtime_lookup_used" in warnings:
        origins.append("database_runtime_introspection")
    return _unique_preserve_order(origins)


def derive_runtime_degradation(
    *,
    metadata_sources: list[str],
    warnings: list[str],
) -> list[str]:
    """Translate runtime fallbacks into explicit degraded capability markers."""
    degraded: list[str] = []
    if "metadata_runtime_lookup_used" in warnings:
        degraded.append("semantic_discovery_fallback_database_runtime")
    if "confluence" in metadata_sources and not any(
        source in {"openmetadata", "mcp"} for source in metadata_sources
    ):
        degraded.append("semantic_discovery_fallback_confluence")
    if "rag" in metadata_sources and not any(
        source in {"openmetadata", "mcp"} for source in metadata_sources
    ):
        degraded.append("semantic_discovery_fallback_rag")
    return _unique_preserve_order(degraded)


def register_tool_usage(
    *,
    runtime_state: RuntimeCapabilityState,
    tool_name: str,
    providers: list[str],
) -> None:
    """Record tool and provider usage without duplicating entries."""
    runtime_state.used_tools = _unique_preserve_order([*runtime_state.used_tools, tool_name])
    runtime_state.used_providers = _unique_preserve_order(
        [*runtime_state.used_providers, *providers]
    )


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
