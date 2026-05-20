"""Shared orchestration kernel helpers reused by sync and streaming runtimes."""

from __future__ import annotations

import json
import logging
from typing import Any

from rhetorica_backend.contracts.chat import (
    CapabilityTrace,
    ChatAskRequest,
    RuntimeContextTrace,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.capability_registry import (
    capability_trace_from_runtime,
)
from rhetorica_backend.domain.chat.orchestration.explainability_enricher import (
    ExplainabilityPayloadEnricher,
)
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.models import FinalizationInput
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_error_policy import (
    ToolErrorPolicy,
)
from rhetorica_backend.infra.logging import emit_stdout_event

logger = logging.getLogger(__name__)


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
    if not metadata_table_columns and state.semantic_grounding is None:
        return None
    return RuntimeContextTrace(
        metadata_table_columns=metadata_table_columns,
        semantic_grounding=state.semantic_grounding,
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
    if state.analysis_plan is not None:
        payload["analysis_plan"] = state.analysis_plan.model_dump(mode="json")
    if state.worker_results:
        payload["worker_results"] = [
            result.model_dump(mode="json")
            for result in state.worker_results[-5:]
        ]
    if state.checkpoint is not None:
        payload["checkpoint_digest"] = state.checkpoint.digest_payload()
    return payload


def execution_summaries_from_state(
    state: ToolOrchestrationState,
    include_chartable: bool = True,
) -> list[dict[str, Any]]:
    del include_chartable
    summaries: list[dict[str, Any]] = []
    for attempt in successful_execution_attempts(state)[-3:]:
        preview = attempt.execution.preview
        row_count = (
            attempt.execution.row_count
            if attempt.execution.row_count is not None
            else len(preview.rows)
        )
        entry = {
            "execution_id": attempt.execution.execution_id,
            "connector_name": attempt.connector_name,
            "status": attempt.execution.status,
            "sql": attempt.sql,
            "row_count": row_count,
            "columns": preview.columns[:10],
        }
        summaries.append(entry)
    return summaries


def synthesize_evidence(
    *,
    state: ToolOrchestrationState,
    primary_execution: ExecuteSqlResponse | None = None,
) -> list[dict[str, Any]]:
    return ExplainabilityPayloadEnricher.synthesize_evidence(
        state=state,
        primary_execution=primary_execution,
    )


def allowed_evidence_references_from_state(
    state: ToolOrchestrationState,
) -> dict[str, list[str]]:
    references: dict[str, list[str]] = {}
    for item in synthesize_evidence(state=state):
        evidence_type = to_optional_str(item.get("type"))
        reference = to_optional_str(item.get("reference"))
        if evidence_type is None or reference is None:
            continue
        refs = references.setdefault(evidence_type, [])
        if reference not in refs:
            refs.append(reference)
    return references


def build_finalizer_context(
    *,
    raw_final_text: str,
    state: ToolOrchestrationState,
    uncertainty_flags: list[str] | None = None,
    metric_definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runtime_context = runtime_context_payload_from_state(state)
    finalization_input = FinalizationInput.model_validate(
        {
            "raw_final_text": raw_final_text,
            "uncertainty_flags": list(uncertainty_flags or []),
            "turn_context": state.build_turn_context_snapshot().model_dump(mode="json"),
        }
    )
    context = finalization_input.model_dump(mode="json")
    # Keep finalizer payload compact; state-derived artifacts below are already
    # normalized and less error-prone than the full turn snapshot.
    context.pop("turn_context", None)
    metadata_context = {
        "metadata_sources": sorted(state.metadata_sources),
        "context_tables": state.context_tables[:20],
        "runtime_context": runtime_context,
    }
    if metric_definitions:
        metadata_context["metric_definitions"] = metric_definitions
    elif state.discovered_metrics:
        metadata_context["metric_definitions"] = state.discovered_metrics
    context.update(
        {
            "generated_sql": state.generated_sql,
            "selected_database_connector": state.selected_database_connector,
            "execution_summary": execution_summaries_from_state(
                state, include_chartable=False
            ),
            "visualization_candidate": (
                state.visualization_candidate.model_dump(mode="json")
                if state.visualization_candidate is not None
                else None
            ),
            "metadata_context": metadata_context,
            "semantic_grounding": runtime_context.get("semantic_grounding")
            if isinstance(runtime_context, dict)
            else None,
            "prompt_version": state.prompt_version,
            "allowed_evidence_references": allowed_evidence_references_from_state(state),
        }
    )
    return context


def merge_uncertainty_flags(*flag_groups: list[str] | None) -> list[str]:
    merged: list[str] = []
    for group in flag_groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value and value not in merged:
                merged.append(value)
    return merged


def execution_recovery_prompt(
    *,
    tool_result: dict[str, Any],
    successful_attempts: list[SqlExecutionAttempt],
) -> str:
    summaries = []
    for attempt in successful_attempts[-3:]:
        preview = attempt.execution.preview
        columns = ", ".join(preview.columns[:6]) if preview.columns else "none"
        row_count = (
            attempt.execution.row_count
            if attempt.execution.row_count is not None
            else len(preview.rows)
        )
        summaries.append(
            f"- execution_id={attempt.execution.execution_id}; "
            f"connector={attempt.connector_name or 'unknown'}; "
            f"rows={row_count}; columns={columns}; "
            f"sql={attempt.sql}"
        )
    return (
        "A later tool attempt failed after successful SQL execution.\n"
        f"Latest tool error: {ToolErrorPolicy.tool_result_user_message(tool_result)}\n"
        "Successful executions still available:\n"
        + "\n".join(summaries)
        + "\nReturn the best final answer from the successful executions. "
        "If the failed attempt adds uncertainty, mention it briefly without discarding the successful result."
    )


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
    return (
        "I could not produce a schema-valid answer. "
        "Please retry the question with a narrower scope."
    )


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
            "| "
            + " | ".join(markdown_table_cell(value) for value in visible_values)
            + " |"
        )
    return "\n".join([header, divider, *body])


def markdown_table_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def looks_like_nonfinal_raw_payload(text: str | None) -> bool:
    return FinalPayloadPolicy.looks_like_nonfinal_raw_payload(text)


def preserve_raw_markdown_answer(
    *,
    raw_final_text: str,
    parsed_payload: dict[str, Any],
    parsed_raw_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return FinalPayloadPolicy.preserve_raw_markdown_answer_from_payload(
        raw_final_text=raw_final_text,
        parsed_raw_payload=parsed_raw_payload,
        parsed_payload=parsed_payload,
    )


def extract_uncertainty_flags(llm_payload: dict[str, Any]) -> list[str]:
    return FinalPayloadPolicy.extract_uncertainty_flags(llm_payload)


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


def has_needs_user_clarification_flag(llm_payload: dict[str, Any]) -> bool:
    return FinalPayloadPolicy.has_needs_user_clarification_flag(llm_payload)


def is_acceptable_final_payload(
    *,
    llm_payload: dict[str, Any],
    state: ToolOrchestrationState,
    request: ChatAskRequest,
) -> bool:
    return FinalPayloadPolicy.is_acceptable_final_payload(
        llm_payload=llm_payload,
        state=state,
        request=request,
    )


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
) -> dict[str, Any]:
    latest_successful_attempt = latest_successful_execution_attempt(state)
    return {
        "answer": "LLM request failed. Check connector URL, TLS settings, and API key.",
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
    content = extract_message_content(message)
    if content:
        result["content"] = content
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        result["tool_calls"] = tool_calls
    return result


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
    current_tool_names = tool_names(tools)
    if (
        not request.run_sql
        or not db_connectors
        or "run_sql" in current_tool_names
        or not (
            state.context_tables
            or state.metadata_table_columns
            or state.execution_zone is not None
        )
    ):
        return tools

    augmented_tools = [
        *tools,
        *PromptBuilder.build_tools_from_names(["run_sql"]),
    ]
    if "run_sql" not in state.capability_state.available_tools:
        state.capability_state.available_tools.append("run_sql")
    return augmented_tools


def protocol_repair_message(tool_names_list: list[str]) -> dict[str, str]:
    exact_lookup_hint = ""
    if "fetch_metadata_tables" in tool_names_list:
        exact_lookup_hint = (
            " For exact table/entity names, prefixes, and schema lookups, use "
            "fetch_metadata_tables instead of search_metadata_catalog."
        )
    return {
        "role": "system",
        "content": (
            "The previous assistant turn completed without an explicit tool request. "
            "Return JSON only. Decide the next action explicitly. "
            "Set next_action to tool_call, final_answer, or needs_user_clarification. "
            "If next_action=tool_call, fill tool_request with one allowed tool and arguments. "
            "If no tool is needed, copy the final user-facing answer into answer_markdown. "
            "Do not answer with progress text like 'searching' or 'looking'. "
            "If the previous turn includes draft SQL, promises another step, or says it will "
            "check/search/find something next, choose tool_call instead of final_answer. "
            "Do not repackage an unfinished plan as final_answer. "
            "If metadata from the current turn is already enough to explain a table/entity, "
            "choose final_answer and summarize the matches."
            + exact_lookup_hint
            + f" Allowed tools: {', '.join(tool_names_list)}."
        ),
    }


def forced_tool_repair_message() -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Protocol escalation: the previous repair response was invalid. "
            "Do not narrate. Your next turn must request one or more runtime tools now. "
            "For exact table/entity names, prefixes, and schema lookups, use "
            "fetch_metadata_tables rather than search_metadata_catalog."
        ),
    }


def strip_thinking_tags(content: str) -> str:
    return FinalPayloadPolicy.strip_thinking_tags(content)


def is_capabilities_question(question: str) -> bool:
    normalized = question.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "что ты умеешь",
            "что умеешь",
            "какие у тебя возможности",
            "что ты можешь",
            "what can you do",
            "capabilities",
        )
    )


def build_capabilities_answer(
    *,
    has_db_connectors: bool,
    metadata_configured: bool,
    rag_configured: bool,
    confluence_configured: bool,
    jira_configured: bool = False,
) -> str:
    capabilities: list[str] = []
    if metadata_configured:
        capabilities.append("- искать таблицы, колонки и lineage в метаданных")
    if confluence_configured:
        capabilities.append("- искать внутренние инструкции и страницы в Confluence")
    if jira_configured:
        capabilities.append("- искать Jira-задачи, читать их и заводить новые по явной команде")
    if rag_configured:
        capabilities.append("- доставать проверенные инсайты из RAG-базы")
    if has_db_connectors:
        capabilities.append("- собирать и выполнять read-only SQL по подключенным БД")
    else:
        capabilities.append("- собирать read-only SQL черновики без выполнения")
    return "Я умею:\n" + "\n".join(capabilities)
