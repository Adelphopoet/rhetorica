"""Stable namespaces for model-controlled deferred tool loading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolGroupSpec:
    """One stable namespace whose schemas may be loaded together."""

    name: str
    description: str
    tool_names: tuple[str, ...]


TOOL_GROUP_SPECS: tuple[ToolGroupSpec, ...] = (
    ToolGroupSpec(
        name="data_analysis",
        description="Execute governed read-only SQL and build visualizations from execution results.",
        tool_names=("run_sql", "build_visualization"),
    ),
    ToolGroupSpec(
        name="knowledge_search",
        description="Search reusable insights and connected internal documentation.",
        tool_names=(
            "search_rag_insights",
            "search_documentation",
            "read_documentation_page",
        ),
    ),
    ToolGroupSpec(
        name="source_control",
        description="Search and inspect files, trees, paths, and blame in connected GitLab projects.",
        tool_names=(
            "search_gitlab_content",
            "find_gitlab_paths",
            "read_gitlab_file",
            "list_gitlab_tree",
            "get_gitlab_file_blame",
        ),
    ),
    ToolGroupSpec(
        name="workflow_orchestration",
        description="Inspect Airflow DAGs, runs, tasks, logs, import errors, and health.",
        tool_names=(
            "search_airflow_dags",
            "get_airflow_dag",
            "list_airflow_dag_runs",
            "get_airflow_dag_run",
            "list_airflow_task_instances",
            "get_airflow_task_logs",
            "list_airflow_import_errors",
            "get_airflow_health",
        ),
    ),
    ToolGroupSpec(
        name="kubernetes_workloads",
        description="Inspect Kubernetes pods, deployments, rollouts, replica sets, stateful sets, and jobs.",
        tool_names=(
            "list_kubernetes_pods",
            "get_kubernetes_pod",
            "list_kubernetes_deployments",
            "get_kubernetes_deployment",
            "get_kubernetes_rollout_status",
            "list_kubernetes_replicasets",
            "list_kubernetes_statefulsets",
            "list_kubernetes_jobs",
            "get_kubernetes_job",
        ),
    ),
    ToolGroupSpec(
        name="kubernetes_diagnostics",
        description="Inspect Kubernetes namespace status, pod logs, events, and resource quotas.",
        tool_names=(
            "get_kubernetes_namespace_status",
            "read_kubernetes_pod_logs",
            "list_kubernetes_events",
            "list_kubernetes_resource_quotas",
        ),
    ),
    ToolGroupSpec(
        name="kubernetes_networking",
        description="Inspect Kubernetes ingresses, services, and endpoints.",
        tool_names=(
            "list_kubernetes_ingresses",
            "list_kubernetes_services",
            "list_kubernetes_endpoints",
        ),
    ),
    ToolGroupSpec(
        name="observability_metrics",
        description="List approved VictoriaMetrics templates and run instant or range metric queries.",
        tool_names=(
            "list_victoriametrics_metric_templates",
            "query_victoriametrics_instant",
            "query_victoriametrics_range",
        ),
    ),
    ToolGroupSpec(
        name="mcp_connectors",
        description="List tools for an exact MCP connector and load or unload exact selected MCP schemas.",
        tool_names=("list_mcp_tools", "load_mcp_tools", "unload_mcp_tools"),
    ),
    ToolGroupSpec(
        name="file_workspace",
        description="Inspect, transform, publish, and export files attached to the web conversation.",
        tool_names=(
            "list_files",
            "inspect_file",
            "run_file_code",
            "publish_files",
            "export_sql_result",
        ),
    ),
    ToolGroupSpec(
        name="tool_memory",
        description=(
            "Access retained tool results from earlier conversation turns and manage "
            "durable model-owned evidence. Load this group when an earlier result may "
            "help with the current request, especially before repeating an external call "
            "only to recover details that may already be retained. The model chooses "
            "whether to read, remember, forget, or repeat; the backend does not rank "
            "artifacts or automatically promote evidence."
        ),
        tool_names=(
            "inspect_tool_artifact",
            "read_tool_artifact",
            "find_tool_artifact_text",
            "list_recent_tool_artifacts",
            "remember_tool_artifact",
            "read_tool_evidence",
            "forget_tool_evidence",
        ),
    ),
)

TOOL_GROUP_BY_NAME: dict[str, ToolGroupSpec] = {
    group.name: group for group in TOOL_GROUP_SPECS
}
TOOL_GROUP_BY_TOOL_NAME: dict[str, ToolGroupSpec] = {
    tool_name: group for group in TOOL_GROUP_SPECS for tool_name in group.tool_names
}


def build_available_tool_groups(
    definitions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, ...]]]:
    """Index permitted definitions and group them without selecting by intent."""
    definitions_by_name: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        function = definition.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("Tool definition is missing an exact function name")
        if name not in TOOL_GROUP_BY_TOOL_NAME:
            raise ValueError(f"Tool '{name}' has no deferred tool group")
        definitions_by_name[name] = definition

    groups: dict[str, tuple[str, ...]] = {}
    for group in TOOL_GROUP_SPECS:
        available_names = tuple(
            name for name in group.tool_names if name in definitions_by_name
        )
        if available_names:
            groups[group.name] = available_names
    return definitions_by_name, groups


def render_available_tool_group_catalog(
    groups: dict[str, tuple[str, ...]],
) -> str:
    """Render the compact active namespace catalog without deferred schemas."""
    lines = ["Available ordinary tool groups (exact active names):"]
    lines.extend(
        f"- {group_name}: {TOOL_GROUP_BY_NAME[group_name].description}"
        for group_name in groups
    )
    return "\n".join(lines)


def validate_tool_group_registry(*, max_group_size: int = 10) -> None:
    """Fail fast when stable names overlap or a namespace grows too wide."""
    group_names = [group.name for group in TOOL_GROUP_SPECS]
    if len(group_names) != len(set(group_names)):
        raise ValueError("Deferred tool group names must be unique")

    tool_names = [tool for group in TOOL_GROUP_SPECS for tool in group.tool_names]
    if len(tool_names) != len(set(tool_names)):
        raise ValueError("Every deferred tool must belong to exactly one group")

    oversized = [
        group.name for group in TOOL_GROUP_SPECS if len(group.tool_names) > max_group_size
    ]
    if oversized:
        raise ValueError(f"Deferred tool groups exceed size {max_group_size}: {oversized}")


validate_tool_group_registry()
