"""Shared orchestration kernel helpers reused by sync and streaming runtimes."""

from __future__ import annotations

import json
import logging
from typing import Any

from rhetorica_backend.contracts.analysis_document import AnalysisDocument
from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    McpToolActivationTrace,
    RuntimeContextTrace,
    ToolActivationTrace,
    WorkspaceSkillActivationTrace,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    capability_trace_from_runtime,
)
from rhetorica_backend.domain.chat.orchestration.explainability_enricher import (
    ExplainabilityPayloadEnricher,
)
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.llm_failure import (
    LlmFailureMessageFormatter,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.infra.logging import emit_stdout_event

logger = logging.getLogger(__name__)

TECHNICAL_FINALIZATION_FAILURE_ANSWER = (
    "Technical error: the LLM endpoint returned no user-facing answer. Please retry."
)


def emit_diagnostic_event(event: str, **fields: Any) -> None:
    try:
        emit_stdout_event(event, **fields)
    except Exception:
        logger.exception("Failed to emit diagnostic stdout event")


def successful_execution_attempts(
    state: ToolOrchestrationState,
) -> list[SqlExecutionAttempt]:
    return FinalPayloadPolicy.successful_execution_attempts(state)


def latest_successful_execution_attempt(
    state: ToolOrchestrationState,
) -> SqlExecutionAttempt | None:
    attempts = successful_execution_attempts(state)
    if attempts:
        return attempts[-1]
    return None


def analysis_document_from_visualization_candidates(
    *,
    state: Any,
    execution_attempts: list[Any] | None = None,
) -> dict[str, Any] | None:
    candidates = list(getattr(state, "visualization_candidates", []) or [])
    if not candidates:
        candidate = getattr(state, "visualization_candidate", None)
        if candidate is not None:
            candidates = [candidate]
    if not candidates:
        return None

    attempts = (
        execution_attempts
        if execution_attempts is not None
        else successful_execution_attempts(state)
    )
    executions_by_id = {attempt.execution.execution_id: attempt.execution for attempt in attempts}
    blocks: list[dict[str, Any]] = []
    planner_modes: set[str] = set()
    for visualization in candidates:
        execution = executions_by_id.get(visualization.data_source.execution_id)
        planner_modes.add(visualization.planner_mode)
        blocks.append({
            "kind": "chart",
            "title": visualization.title,
            "description": visualization.description,
            "insight": visualization.summary,
            "source_execution_id": visualization.data_source.execution_id,
            "preview": (
                execution.preview.model_dump(mode="json") if execution is not None else None
            ),
            "visualization": visualization.model_dump(mode="json"),
        })

    planner_mode = next(iter(planner_modes)) if len(planner_modes) == 1 else "mixed"
    try:
        document = AnalysisDocument.model_validate({
            "document_version": "v1",
            "title": "Analysis report",
            "executive_summary": None,
            "blocks": blocks,
            "warnings": list(getattr(state, "warnings", [])),
            "provenance": {
                "source_type": "chat_answer",
                "execution_id": candidates[-1].data_source.execution_id,
                "planner_mode": planner_mode,
            },
        })
    except Exception:
        return None
    return document.model_dump(mode="json")


def execution_attempt_by_reference(
    *,
    state: ToolOrchestrationState,
    reference: str,
) -> SqlExecutionAttempt | None:
    return FinalPayloadPolicy.execution_attempt_by_reference(
        state=state,
        reference=reference,
    )


def resolve_execution_attempt_from_payload(
    *,
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
) -> SqlExecutionAttempt | None:
    evidence = llm_payload.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "execution_result":
                continue
            reference = str(item.get("reference") or "").strip()
            attempt = execution_attempt_by_reference(
                state=state,
                reference=reference,
            )
            if attempt is not None:
                return attempt
    return latest_successful_execution_attempt(state)


def resolve_execution_from_payload(
    *,
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
) -> ExecuteSqlResponse | None:
    attempt = resolve_execution_attempt_from_payload(
        llm_payload=llm_payload,
        state=state,
    )
    if attempt is not None:
        return attempt.execution
    execution_payload = llm_payload.get("execution")
    if isinstance(execution_payload, dict):
        try:
            return ExecuteSqlResponse.model_validate(execution_payload)
        except Exception:
            logger.debug("Failed to parse execution payload from llm payload")
    return state.execution


def resolve_sql_from_payload(
    *,
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
    execution_attempt: SqlExecutionAttempt | None,
) -> str | None:
    if execution_attempt is not None and execution_attempt.sql.strip():
        return execution_attempt.sql.strip()
    sql = llm_payload.get("sql")
    if isinstance(sql, str) and sql.strip():
        return sql.strip()
    return state.generated_sql


def resolve_selected_connector_from_payload(
    *,
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
    execution_attempt: SqlExecutionAttempt | None,
) -> str | None:
    if execution_attempt is not None and execution_attempt.connector_name:
        return execution_attempt.connector_name
    target = llm_payload.get("target")
    if isinstance(target, dict):
        connector_name = target.get("connector_name")
        if isinstance(connector_name, str) and connector_name.strip():
            return connector_name.strip()
    return state.selected_database_connector


def build_capability_trace_from_state(
    *,
    state: ToolOrchestrationState,
    metadata_sources: list[str],
    warnings: list[str],
    execution: ExecuteSqlResponse | None,
) -> CapabilityTrace:
    capability_state = state.capability_state
    return capability_trace_from_runtime(
        available_tools=capability_state.available_tools,
        used_tools=capability_state.used_tools,
        used_providers=capability_state.used_providers,
        metadata_sources=metadata_sources,
        warnings=warnings,
        has_execution=execution is not None,
        degraded_capabilities=capability_state.degraded_capabilities,
        policy_decisions=capability_state.policy_decisions,
        runtime_context=runtime_context_model_from_state(state),
    )


def runtime_context_model_from_state(
    state: ToolOrchestrationState,
) -> RuntimeContextTrace | None:
    metadata_table_columns = {
        table_name: sorted(columns)
        for table_name, columns in state.metadata_table_columns.items()
        if columns
    }
    workspace_skill_activations = [
        WorkspaceSkillActivationTrace(
            skill_id=skill["id"],
            content_sha256=skill["content_sha256"],
        )
        for skill in state.workspace_skills_read
        if isinstance(skill, dict)
        and isinstance(skill.get("id"), str)
        and isinstance(skill.get("content_sha256"), str)
    ]
    mcp_tools = [
        McpToolActivationTrace(
            connector_name=connector_name,
            remote_tool_names=list(remote_tool_names),
        )
        for connector_name, remote_tool_names in state.loaded_mcp_remote_tool_names.items()
        if remote_tool_names
    ]
    return RuntimeContextTrace(
        metadata_table_columns=metadata_table_columns,
        semantic_grounding=state.semantic_grounding,
        workspace_skill_activations=workspace_skill_activations,
        tool_activation=ToolActivationTrace(
            group_names=list(state.loaded_tool_group_names),
            mcp_tools=mcp_tools,
        ),
    )


def runtime_context_payload_from_state(
    state: ToolOrchestrationState,
) -> dict[str, Any]:
    runtime_context = runtime_context_model_from_state(state)
    payload = (
        runtime_context.model_dump(mode="json", by_alias=True)
        if runtime_context is not None
        else {}
    )
    return payload


def deterministic_finalizer_answer(
    state: ToolOrchestrationState,
) -> str:
    latest_successful_attempt = latest_successful_execution_attempt(state)
    if latest_successful_attempt is not None:
        preview_markdown = execution_preview_markdown(latest_successful_attempt.execution)
        if preview_markdown:
            return f"Result preview:\n\n{preview_markdown}"
        return "Query completed successfully."
    if state.semantic_grounding is not None:
        chosen_table = state.semantic_grounding.chosen_table_fqn
        rationale = state.semantic_grounding.selection_rationale
        if chosen_table and rationale:
            return f"{rationale}"
        if chosen_table:
            return f"Metadata lookup selected `{chosen_table}` as the best governed source."
    if state.context_tables:
        listed_tables = ", ".join(state.context_tables[:3])
        return f"Metadata lookup completed successfully for: {listed_tables}."
    return TECHNICAL_FINALIZATION_FAILURE_ANSWER


def execution_preview_markdown(execution: ExecuteSqlResponse) -> str:
    columns = execution.preview.columns[:8]
    rows = execution.preview.rows[:10]
    if not columns or not rows:
        return ""

    header = "| " + " | ".join(markdown_table_cell(column) for column in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        visible_values = row[: len(columns)]
        if len(visible_values) < len(columns):
            visible_values = [*visible_values, *([""] * (len(columns) - len(visible_values)))]
        body.append(
            "| " + " | ".join(markdown_table_cell(value) for value in visible_values) + " |"
        )
    return "\n".join([header, divider, *body])


def markdown_table_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def augment_payload_with_explainability(
    *,
    payload: dict[str, Any],
    state: ToolOrchestrationState,
) -> dict[str, Any]:
    return ExplainabilityPayloadEnricher.augment_payload(
        payload=payload,
        state=state,
        resolve_execution_from_payload=lambda llm_payload, current_state: (
            resolve_execution_from_payload(
                llm_payload=llm_payload,
                state=current_state,
            )
        ),
    )


def evidence_from_tool_result_item(
    *,
    item: dict[str, Any],
    state: ToolOrchestrationState,
) -> dict[str, Any] | None:
    return ExplainabilityPayloadEnricher.evidence_from_tool_result_item(
        item=item,
        state=state,
    )


def semantic_candidate_snippet_from_state(
    *,
    state: ToolOrchestrationState,
    reference: str,
) -> str | None:
    return ExplainabilityPayloadEnricher.semantic_candidate_snippet_from_state(
        state=state,
        reference=reference,
    )


def semantic_item_snippet(item: dict[str, Any]) -> str | None:
    return ExplainabilityPayloadEnricher.semantic_item_snippet(item)


def execution_snippet(execution: ExecuteSqlResponse | None) -> str:
    return ExplainabilityPayloadEnricher.execution_snippet(execution)


def synthesize_claims(answer: str) -> list[dict[str, Any]]:
    return ExplainabilityPayloadEnricher.synthesize_claims(answer)


def synthesize_claim_links(
    *,
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return ExplainabilityPayloadEnricher.synthesize_claim_links(
        claims=claims,
        evidence=evidence,
    )


def to_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def to_list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def to_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def has_supported_evidence(
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
) -> bool:
    return FinalPayloadPolicy.has_supported_evidence(llm_payload, state)


def has_non_request_evidence(llm_payload: dict[str, Any]) -> bool:
    return FinalPayloadPolicy.has_non_request_evidence(llm_payload)


def requires_metadata_lookup(request: ChatAskRequest) -> bool:
    return FinalPayloadPolicy.requires_metadata_lookup(request)


def llm_request_failed_payload(
    state: ToolOrchestrationState,
    connector_name: str | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    latest_successful_attempt = latest_successful_execution_attempt(state)
    failure = error or RuntimeError("Unknown LLM error")
    return {
        "answer": LlmFailureMessageFormatter.format(
            connector_name=connector_name,
            error=failure,
        ),
        "sql": (
            latest_successful_attempt.sql
            if latest_successful_attempt is not None
            else state.generated_sql
        ),
        "target": None,
        "selected_database_connector": (
            latest_successful_attempt.connector_name
            if latest_successful_attempt is not None
            else state.selected_database_connector
        ),
        "metadata_sources": sorted(state.metadata_sources),
        "context_tables": state.context_tables[:20],
        "runtime_context": runtime_context_payload_from_state(state),
        "execution": (
            latest_successful_attempt.execution.model_dump(mode="json")
            if latest_successful_attempt is not None
            else (state.execution.model_dump(mode="json") if state.execution else None)
        ),
        "visualization": None,
        "optimization_trace": state.optimization_trace.model_dump(mode="json"),
        "capability_trace": build_capability_trace_from_state(
            state=state,
            metadata_sources=sorted(state.metadata_sources),
            warnings=state.warnings,
            execution=(
                latest_successful_attempt.execution
                if latest_successful_attempt is not None
                else state.execution
            ),
        ).model_dump(mode="json"),
        "prompt_version": state.prompt_version,
        "warnings": state.warnings,
    }


def assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"role": "assistant"}
    content = strip_thinking_tags(extract_message_content(message)).strip()
    if content:
        result["content"] = content
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        result["tool_calls"] = [
            canonical_tool_call_for_history(tool_call)
            for tool_call in tool_calls
            if isinstance(tool_call, dict)
        ]
        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            result["reasoning_content"] = reasoning_content
    return result


def canonical_tool_call_for_history(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Replay the exact argument object accepted by the runtime as valid JSON."""
    normalized = dict(tool_call)
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return normalized
    normalized_function = dict(function)
    normalized_function["arguments"] = json.dumps(
        extract_tool_arguments(tool_call),
        ensure_ascii=False,
        sort_keys=True,
    )
    normalized["function"] = normalized_function
    return normalized


def extract_message_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        return "\n".join(chunks)
    return ""


def extract_tool_name(tool_call: dict[str, Any]) -> str | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) and name.strip() else None


def extract_tool_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return {}
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def synthetic_assistant_tool_message(
    *,
    content_for_history: str,
    tool_name: str,
    arguments: dict[str, Any],
    tool_call_id: str,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }
    content = to_optional_str(content_for_history)
    if content:
        message["content"] = content
    return message


def tool_names(tools: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def ensure_dynamic_tools(
    *,
    tools: list[dict[str, Any]],
    request: ChatAskRequest,
    db_connectors: list[ConnectorItem],
    state: ToolOrchestrationState,
) -> list[dict[str, Any]]:
    _ = request, db_connectors
    augmented_tools = list(tools)
    current_tool_names = set(tool_names(augmented_tools))
    for name, definition in state.loaded_tool_definitions.items():
        if name in current_tool_names:
            continue
        augmented_tools.append(definition)
        current_tool_names.add(name)
        if name not in state.capability_state.available_tools:
            state.capability_state.available_tools.append(name)

    for exposed_name, definition in state.loaded_mcp_tool_definitions.items():
        if exposed_name in current_tool_names:
            continue
        augmented_tools.append(definition)
        current_tool_names.add(exposed_name)
        if exposed_name not in state.capability_state.available_tools:
            state.capability_state.available_tools.append(exposed_name)

    return augmented_tools


def strip_thinking_tags(content: str) -> str:
    return FinalPayloadPolicy.strip_thinking_tags(content)
