from __future__ import annotations

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.shared_kernel import ensure_dynamic_tools
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_group_capability_service import (
    ToolGroupCapabilityService,
)
from rhetorica_backend.domain.chat.orchestration.tool_group_registry import (
    TOOL_GROUP_BY_TOOL_NAME,
    TOOL_GROUP_SPECS,
    build_available_tool_groups,
    validate_tool_group_registry,
)


def _names(definitions: list[dict]) -> list[str]:
    return [definition["function"]["name"] for definition in definitions]


def test_tool_group_registry_has_unique_small_namespaces() -> None:
    validate_tool_group_registry(max_group_size=10)
    members = [name for group in TOOL_GROUP_SPECS for name in group.tool_names]
    assert len(members) == len(set(members))
    assert set(members) == set(TOOL_GROUP_BY_TOOL_NAME)


def test_exact_group_loading_is_model_controlled() -> None:
    deferred = PromptBuilder.build_tools_from_names([
        "search_gitlab_content",
        "read_gitlab_file",
        "search_airflow_dags",
        "get_airflow_health",
    ])
    definitions, groups = build_available_tool_groups(deferred)
    state = ToolOrchestrationState(
        deferred_tool_definitions=definitions,
        available_tool_groups=groups,
    )
    service = ToolGroupCapabilityService()

    assert service.load_group(
        arguments={"group_name": "source_control"}, state=state
    )["loaded_tool_names"] == ["search_gitlab_content", "read_gitlab_file"]
    assert service.load_group(
        arguments={"group_name": "workflow_orchestration"}, state=state
    )["loaded_tool_names"] == ["search_airflow_dags", "get_airflow_health"]

    tools = ensure_dynamic_tools(
        tools=PromptBuilder.build_tools_from_names([
            "load_tool_group",
            "unload_tool_group",
        ]),
        request=ChatAskRequest(question="inspect", workspace_id="default", run_sql=False),
        db_connectors=[],
        state=state,
    )
    assert _names(tools) == [
        "load_tool_group",
        "unload_tool_group",
        "search_gitlab_content",
        "read_gitlab_file",
        "search_airflow_dags",
        "get_airflow_health",
    ]


def test_unavailable_group_is_not_fuzzily_substituted() -> None:
    deferred = PromptBuilder.build_tools_from_names(["search_gitlab_content"])
    definitions, groups = build_available_tool_groups(deferred)
    state = ToolOrchestrationState(
        deferred_tool_definitions=definitions,
        available_tool_groups=groups,
    )

    result = ToolGroupCapabilityService.load_group(
        arguments={"group_name": "source-controll"},
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "tool_group_unavailable"


def test_run_sql_is_deferred_until_data_analysis_is_loaded() -> None:
    deferred = PromptBuilder.build_tools_from_names(["run_sql", "build_visualization"])
    definitions, groups = build_available_tool_groups(deferred)
    state = ToolOrchestrationState(
        deferred_tool_definitions=definitions,
        available_tool_groups=groups,
    )
    initial = PromptBuilder.build_tools_from_names(["load_tool_group", "unload_tool_group"])
    request = ChatAskRequest(question="query", workspace_id="default", run_sql=True)

    before = ensure_dynamic_tools(
        tools=initial,
        request=request,
        db_connectors=[],
        state=state,
    )
    assert "run_sql" not in _names(before)

    ToolGroupCapabilityService.load_group(
        arguments={"group_name": "data_analysis"}, state=state
    )
    after = ensure_dynamic_tools(
        tools=initial,
        request=request,
        db_connectors=[],
        state=state,
    )
    assert "run_sql" in _names(after)
