"""Tool-first capability registry and trace helpers for chat orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from rhetorica_backend.contracts.chat import CapabilityTrace, RuntimeContextTrace

from .turn_policy import TurnPolicy

_TOOL_USAGE_LOCK = Lock()


@dataclass(frozen=True)
class ToolCapabilitySpec:
    """Registry record for one runtime tool."""

    name: str
    family: str
    primary_provider: str
    fallback_providers: tuple[str, ...] = ()


TOOL_CAPABILITY_REGISTRY: dict[str, ToolCapabilitySpec] = {
    "list_tool_groups": ToolCapabilitySpec(
        name="list_tool_groups",
        family="tool_discovery",
        primary_provider="backend",
    ),
    "load_tool_group": ToolCapabilitySpec(
        name="load_tool_group",
        family="tool_discovery",
        primary_provider="backend",
    ),
    "unload_tool_group": ToolCapabilitySpec(
        name="unload_tool_group",
        family="tool_discovery",
        primary_provider="backend",
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
    "search_documentation": ToolCapabilitySpec(
        name="search_documentation",
        family="docs_retrieval",
        primary_provider="documentation",
    ),
    "read_documentation_page": ToolCapabilitySpec(
        name="read_documentation_page",
        family="docs_retrieval",
        primary_provider="documentation",
    ),
    "search_gitlab_content": ToolCapabilitySpec(
        name="search_gitlab_content",
        family="source_code_retrieval",
        primary_provider="gitlab",
    ),
    "find_gitlab_paths": ToolCapabilitySpec(
        name="find_gitlab_paths",
        family="source_code_retrieval",
        primary_provider="gitlab",
    ),
    "read_gitlab_file": ToolCapabilitySpec(
        name="read_gitlab_file",
        family="source_code_retrieval",
        primary_provider="gitlab",
    ),
    "list_gitlab_tree": ToolCapabilitySpec(
        name="list_gitlab_tree",
        family="source_code_retrieval",
        primary_provider="gitlab",
    ),
    "get_gitlab_file_blame": ToolCapabilitySpec(
        name="get_gitlab_file_blame",
        family="source_code_retrieval",
        primary_provider="gitlab",
    ),
    "search_airflow_dags": ToolCapabilitySpec(
        name="search_airflow_dags",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "get_airflow_dag": ToolCapabilitySpec(
        name="get_airflow_dag",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "list_airflow_dag_runs": ToolCapabilitySpec(
        name="list_airflow_dag_runs",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "get_airflow_dag_run": ToolCapabilitySpec(
        name="get_airflow_dag_run",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "list_airflow_task_instances": ToolCapabilitySpec(
        name="list_airflow_task_instances",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "get_airflow_task_logs": ToolCapabilitySpec(
        name="get_airflow_task_logs",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "list_airflow_import_errors": ToolCapabilitySpec(
        name="list_airflow_import_errors",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "get_airflow_health": ToolCapabilitySpec(
        name="get_airflow_health",
        family="workflow_orchestration",
        primary_provider="airflow",
    ),
    "list_kubernetes_pods": ToolCapabilitySpec(
        name="list_kubernetes_pods",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "get_kubernetes_namespace_status": ToolCapabilitySpec(
        name="get_kubernetes_namespace_status",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "get_kubernetes_pod": ToolCapabilitySpec(
        name="get_kubernetes_pod",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "read_kubernetes_pod_logs": ToolCapabilitySpec(
        name="read_kubernetes_pod_logs",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_deployments": ToolCapabilitySpec(
        name="list_kubernetes_deployments",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "get_kubernetes_deployment": ToolCapabilitySpec(
        name="get_kubernetes_deployment",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "get_kubernetes_rollout_status": ToolCapabilitySpec(
        name="get_kubernetes_rollout_status",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_replicasets": ToolCapabilitySpec(
        name="list_kubernetes_replicasets",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_statefulsets": ToolCapabilitySpec(
        name="list_kubernetes_statefulsets",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_jobs": ToolCapabilitySpec(
        name="list_kubernetes_jobs",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "get_kubernetes_job": ToolCapabilitySpec(
        name="get_kubernetes_job",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_ingresses": ToolCapabilitySpec(
        name="list_kubernetes_ingresses",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_services": ToolCapabilitySpec(
        name="list_kubernetes_services",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_endpoints": ToolCapabilitySpec(
        name="list_kubernetes_endpoints",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_events": ToolCapabilitySpec(
        name="list_kubernetes_events",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_kubernetes_resource_quotas": ToolCapabilitySpec(
        name="list_kubernetes_resource_quotas",
        family="kubernetes_runtime",
        primary_provider="kubernetes",
    ),
    "list_victoriametrics_metric_templates": ToolCapabilitySpec(
        name="list_victoriametrics_metric_templates",
        family="observability_metrics",
        primary_provider="victoriametrics",
    ),
    "query_victoriametrics_instant": ToolCapabilitySpec(
        name="query_victoriametrics_instant",
        family="observability_metrics",
        primary_provider="victoriametrics",
    ),
    "query_victoriametrics_range": ToolCapabilitySpec(
        name="query_victoriametrics_range",
        family="observability_metrics",
        primary_provider="victoriametrics",
    ),
    "read_workspace_skill": ToolCapabilitySpec(
        name="read_workspace_skill",
        family="workspace_instruction",
        primary_provider="workspace_skill",
    ),
    "list_workspace_skill_files": ToolCapabilitySpec(
        name="list_workspace_skill_files",
        family="workspace_instruction",
        primary_provider="workspace_skill",
    ),
    "list_mcp_tools": ToolCapabilitySpec(
        name="list_mcp_tools",
        family="mcp_discovery",
        primary_provider="mcp",
    ),
    "load_mcp_tools": ToolCapabilitySpec(
        name="load_mcp_tools",
        family="mcp_discovery",
        primary_provider="mcp",
    ),
    "unload_mcp_tools": ToolCapabilitySpec(
        name="unload_mcp_tools",
        family="mcp_discovery",
        primary_provider="mcp",
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
    rag_configured: bool,
    documentation_configured: bool = False,
    gitlab_configured: bool = False,
    airflow_configured: bool = False,
    kubernetes_configured: bool = False,
    victoriametrics_configured: bool = False,
    policy: TurnPolicy,
    run_sql_requested: bool,
    chat_skills_configured: bool = False,
    mcp_configured: bool = False,
) -> WorkspaceCapabilitySurface:
    """Derive available tools, degradation, and policy decisions for a turn."""
    available_tools = resolve_available_tool_names(policy)
    degraded_capabilities: list[str] = []
    policy_decisions = [f"prompt_mode:{policy.prompt_mode}"]

    if not documentation_configured:
        degraded_capabilities.append("documentation_retrieval_provider_missing")
    if not gitlab_configured:
        degraded_capabilities.append("source_code_retrieval_provider_missing")
    if not airflow_configured:
        degraded_capabilities.append("workflow_orchestration_provider_missing")
    if not kubernetes_configured:
        degraded_capabilities.append("kubernetes_runtime_provider_missing")
    if not victoriametrics_configured:
        degraded_capabilities.append("observability_metrics_provider_missing")
    if not rag_configured:
        degraded_capabilities.append("insight_retrieval_provider_missing")
    if not has_db_connectors:
        degraded_capabilities.append("sql_execution_provider_missing")
    if not chat_skills_configured:
        degraded_capabilities.append("workspace_instruction_provider_missing")
    if not mcp_configured:
        degraded_capabilities.append("mcp_provider_missing")

    if not run_sql_requested:
        policy_decisions.append("run_sql_disabled_by_request")
    if policy.requires_metadata_lookup:
        policy_decisions.append("metadata_lookup_suggested")
    if run_sql_requested and has_db_connectors and not policy.run_sql_tools_enabled:
        policy_decisions.append("run_sql_not_available_in_current_turn")
    if not available_tools and not policy.mcp_tools_enabled:
        policy_decisions.append("workspace_effectively_llm_only")
    if policy.mcp_tools_enabled:
        policy_decisions.append("mcp_tools_enabled")

    return WorkspaceCapabilitySurface(
        available_tools=available_tools,
        degraded_capabilities=_unique_preserve_order(degraded_capabilities),
        policy_decisions=_unique_preserve_order(policy_decisions),
    )


def resolve_available_tool_names(policy: TurnPolicy) -> list[str]:
    """Resolve available tools from turn policy."""
    names: list[str] = []
    if policy.run_sql_tools_enabled:
        names.append("run_sql")
        names.append("build_visualization")
    if policy.rag_tools_enabled:
        names.append("search_rag_insights")
    if policy.documentation_tools_enabled:
        names.extend(["search_documentation", "read_documentation_page"])
    if policy.gitlab_tools_enabled:
        names.extend([
            "search_gitlab_content",
            "find_gitlab_paths",
            "read_gitlab_file",
            "list_gitlab_tree",
            "get_gitlab_file_blame",
        ])
    if policy.airflow_tools_enabled:
        names.extend([
            "search_airflow_dags",
            "get_airflow_dag",
            "list_airflow_dag_runs",
            "get_airflow_dag_run",
            "list_airflow_task_instances",
            "get_airflow_task_logs",
            "list_airflow_import_errors",
            "get_airflow_health",
        ])
    if policy.kubernetes_tools_enabled:
        names.extend([
            "list_kubernetes_pods",
            "get_kubernetes_namespace_status",
            "get_kubernetes_pod",
            "read_kubernetes_pod_logs",
            "list_kubernetes_deployments",
            "get_kubernetes_deployment",
            "get_kubernetes_rollout_status",
            "list_kubernetes_replicasets",
            "list_kubernetes_statefulsets",
            "list_kubernetes_jobs",
            "get_kubernetes_job",
            "list_kubernetes_ingresses",
            "list_kubernetes_services",
            "list_kubernetes_endpoints",
            "list_kubernetes_events",
            "list_kubernetes_resource_quotas",
        ])
    if policy.victoriametrics_tools_enabled:
        names.extend([
            "list_victoriametrics_metric_templates",
            "query_victoriametrics_instant",
            "query_victoriametrics_range",
        ])
    if policy.workspace_skill_tools_enabled:
        names.extend(["read_workspace_skill", "list_workspace_skill_files"])
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
        degraded_capabilities=_unique_preserve_order([
            *degraded_capabilities,
            *runtime_degradation,
        ]),
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
    if "mcp" in metadata_sources:
        origins.append("governed_metadata")
    if "documentation" in metadata_sources:
        origins.append("internal_documentation")
    if "rag" in metadata_sources:
        origins.append("rag_insights")
    if "gitlab" in metadata_sources:
        origins.append("gitlab_repository")
    if "kubernetes" in metadata_sources:
        origins.append("kubernetes_runtime")
    if "victoriametrics" in metadata_sources:
        origins.append("metrics_timeseries")
    if "airflow" in metadata_sources:
        origins.append("airflow_runtime")
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
    if "documentation" in metadata_sources and not any(
        source == "mcp" for source in metadata_sources
    ):
        degraded.append("semantic_discovery_fallback_documentation")
    if "rag" in metadata_sources and not any(source == "mcp" for source in metadata_sources):
        degraded.append("semantic_discovery_fallback_rag")
    if "gitlab" in metadata_sources and not any(source == "mcp" for source in metadata_sources):
        degraded.append("semantic_discovery_fallback_gitlab")
    return _unique_preserve_order(degraded)


def register_tool_usage(
    *,
    runtime_state: RuntimeCapabilityState,
    tool_name: str,
    providers: list[str],
) -> None:
    """Record tool and provider usage without duplicating entries."""
    # Certified readers execute on separate threads. Serialize only their short
    # audit updates; the tool calls themselves remain concurrent.
    with _TOOL_USAGE_LOCK:
        runtime_state.used_tools = _unique_preserve_order([*runtime_state.used_tools, tool_name])
        runtime_state.used_providers = _unique_preserve_order([
            *runtime_state.used_providers,
            *providers,
        ])


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
