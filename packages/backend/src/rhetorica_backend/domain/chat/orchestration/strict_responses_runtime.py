"""Strict OpenAI Responses tool loop.

The model owns tool selection and final response shape. This runtime only
executes native function calls, validates contracts, runs one contract repair,
and fails closed when the contract is still broken.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import (
    ChatAskRequest,
    ChunkEvent,
    ErrorEvent,
    RuntimeStateEvent,
    StreamEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient, LlmFunctionCall
from rhetorica_backend.domain.chat.orchestration.final_payload_json_extractor import (
    FinalPayloadJsonExtractor,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    final_response_json_schema,
    validate_final_response_payload,
)
from rhetorica_backend.domain.chat.orchestration.finalization_support_services import (
    FinalPayloadBuilderService,
)
from rhetorica_backend.domain.chat.orchestration.models import (
    RuntimeExecutionResult,
    StreamingRuntimeTransportArtifact,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)
from rhetorica_backend.domain.chat.settings import get_max_iterations
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner
from rhetorica_backend.infra.logging import sanitize_for_logging


class ToolArgumentContractError(ValueError):
    """Raised when an LLM function call violates the advertised tool schema."""


class StrictResponsesRuntime:
    """Single strict runtime for sync and streaming chat transports."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        final_payload_builder: FinalPayloadBuilderService | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._final_payload_builder = final_payload_builder or FinalPayloadBuilderService(
            normalize_answer=lambda *, answer, state: answer,
        )
        self._tool_serializer = ToolResultPromptSerializer()
        self._visualization_validator = VisualizationPlanner()

    def run(
        self,
        *,
        llm_client: LlmClient,
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: ToolOrchestrationState,
        tool_executor: Any,
        emit: Callable[[StreamEvent], None] | None = None,
    ) -> RuntimeExecutionResult:
        events: list[StreamEvent] = []

        def record(event: StreamEvent) -> None:
            if emit is not None:
                emit(event)
            else:
                events.append(event)

        tool_contracts = self._tool_contracts(tools)
        seen_calls: set[tuple[str, str]] = set()
        response_schema = final_response_json_schema()
        previous_response_id: str | None = None
        input_items: list[dict[str, Any]] | None = None

        for iteration in range(get_max_iterations()):
            self._logger.info(
                "llm.responses.iteration",
                extra={
                    "iteration": iteration + 1,
                    "tool_count": len(tools),
                    "warnings": list(state.warnings),
                },
            )
            try:
                response = llm_client.responses_completion(
                    messages=messages if input_items is None else None,
                    input_items=input_items,
                    tools=tools,
                    response_schema=response_schema,
                    previous_response_id=previous_response_id,
                    tool_choice="auto" if tools else None,
                )
            except Exception as exc:
                self._logger.exception("strict responses request failed")
                state.add_warning("llm_request_failed")
                message, details = self._llm_request_error_payload(exc)
                record(
                    ErrorEvent(
                        code="llm_request_failed",
                        message=message,
                        details=details,
                    )
                )
                return RuntimeExecutionResult(
                    llm_payload=self._contract_error_payload(
                        state=state,
                        error_code="llm_request_failed",
                        message=message,
                    ),
                    transport_artifact=StreamingRuntimeTransportArtifact(events=events),
                )

            previous_response_id = response.response_id
            for usage in llm_client.collect_usages():
                state.llm_usages.append(usage)

            if response.function_calls:
                input_items = self._execute_function_calls(
                    function_calls=response.function_calls,
                    tool_contracts=tool_contracts,
                    seen_calls=seen_calls,
                    request=request,
                    db_connectors=db_connectors,
                    state=state,
                    tool_executor=tool_executor,
                    record=record,
                )
                terminal_payload = self._terminal_tool_contract_payload(
                    input_items=input_items,
                    state=state,
                )
                if terminal_payload is not None:
                    return RuntimeExecutionResult(
                        llm_payload=terminal_payload,
                        transport_artifact=StreamingRuntimeTransportArtifact(events=events),
                    )
                self._emit_runtime_state(state=state, record=record)
                continue

            final_payload = self._validate_or_repair_final_payload(
                llm_client=llm_client,
                raw_content=response.output_text,
                state=state,
                response_schema=response_schema,
            )
            answer = final_payload.get("answer")
            if isinstance(answer, str) and answer.strip():
                record(ChunkEvent(content=answer.strip()))
            return RuntimeExecutionResult(
                llm_payload=final_payload,
                transport_artifact=StreamingRuntimeTransportArtifact(events=events),
            )

        state.add_warning("llm_tool_loop_limit_reached")
        return RuntimeExecutionResult(
            llm_payload=self._contract_error_payload(
                state=state,
                error_code="llm_tool_loop_limit_reached",
                message="LLM exceeded the maximum number of tool iterations.",
            ),
            transport_artifact=StreamingRuntimeTransportArtifact(events=events),
        )

    def _execute_function_calls(
        self,
        *,
        function_calls: list[LlmFunctionCall],
        tool_contracts: dict[str, dict[str, Any]],
        seen_calls: set[tuple[str, str]],
        request: ChatAskRequest,
        db_connectors: list[ConnectorItem],
        state: ToolOrchestrationState,
        tool_executor: Any,
        record: Callable[[StreamEvent], None],
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for call in function_calls:
            try:
                arguments = self._parse_and_validate_tool_arguments(
                    call=call,
                    tool_contracts=tool_contracts,
                )
                signature = (
                    call.name,
                    json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                )
                self._ensure_not_duplicate_tool_call(
                    signature=signature,
                    seen_calls=seen_calls,
                )
                seen_calls.add(signature)
            except ToolArgumentContractError as exc:
                state.add_warning("llm_tool_contract_violation")
                outputs.append(
                    self._function_call_output(
                        call_id=call.call_id,
                        payload={
                            "ok": False,
                            "error_code": "llm_tool_contract_violation",
                            "user_message": str(exc),
                            "can_continue": False,
                        },
                    )
                )
                continue

            record(
                ToolCallStartEvent(
                    tool_name=call.name,
                    tool_call_id=call.call_id,
                    arguments=arguments,
                )
            )
            state.tool_call_count += 1
            tool_result = tool_executor.execute_tool(
                tool_name=call.name,
                arguments=arguments,
                request=request,
                db_connectors=db_connectors,
                state=state,
            )
            state.record_tool_result(
                tool_name=call.name,
                arguments=arguments,
                result=tool_result,
            )
            record(
                ToolCallEndEvent(
                    tool_name=call.name,
                    tool_call_id=call.call_id,
                    result_summary=self._summarize_tool_result(tool_result),
                    success=bool(tool_result.get("ok", False)),
                )
            )
            if tool_result.get("can_continue") is False:
                outputs.append(
                    self._function_call_output(
                        call_id=call.call_id,
                        payload=tool_result,
                    )
                )
                continue
            envelope = self._tool_serializer.serialize_envelope(
                tool_name=call.name,
                tool_result=tool_result,
            )
            for warning in envelope.warnings:
                state.add_warning(warning)
            outputs.append(
                self._function_call_output(
                    call_id=call.call_id,
                    payload=json.loads(envelope.stable_json()),
                )
            )
        return outputs

    def _validate_or_repair_final_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_content: str,
        state: ToolOrchestrationState,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        payload, errors = self._validate_final_payload(raw_content)
        if payload is not None:
            visualization_errors = self._visualization_errors(payload=payload, state=state)
            if visualization_errors:
                payload = None
                errors = visualization_errors
        if payload is not None:
            state.schema_valid_first_pass += 1
            return self._final_payload_builder.build_final_payload_from_parsed(
                parsed_payload=payload,
                fallback_answer=None,
                state=state,
            )

        state.add_warning("llm_final_payload_repair_requested")
        self._logger.warning(
            "llm.final_payload.validation_failed",
            extra={
                "validation_errors": errors[:10],
                "raw_content": sanitize_for_logging(raw_content),
            },
        )
        repaired_raw = self._repair_final_payload(
            llm_client=llm_client,
            raw_content=raw_content,
            validation_errors=errors,
            response_schema=response_schema,
            state=state,
        )
        repaired_payload, repaired_errors = self._validate_final_payload(repaired_raw)
        if repaired_payload is not None:
            visualization_errors = self._visualization_errors(
                payload=repaired_payload,
                state=state,
            )
            if visualization_errors:
                repaired_payload = None
                repaired_errors = visualization_errors
        if repaired_payload is not None:
            state.schema_repaired += 1
            return self._final_payload_builder.build_final_payload_from_parsed(
                parsed_payload=repaired_payload,
                fallback_answer=None,
                state=state,
            )

        state.schema_failed += 1
        state.add_warning("llm_final_payload_invalid")
        self._logger.warning(
            "llm.final_payload.repair_failed",
            extra={
                "validation_errors": repaired_errors[:10],
                "repaired_content": sanitize_for_logging(repaired_raw),
            },
        )
        return self._contract_error_payload(
            state=state,
            error_code="llm_final_payload_invalid",
            message="LLM returned a final payload that violates the response contract.",
        )

    @staticmethod
    def _llm_request_error_payload(exc: Exception) -> tuple[str, dict[str, Any]]:
        details: dict[str, Any] = {"type": type(exc).__name__}
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            details["status_code"] = status_code
            provider_message = StrictResponsesRuntime._provider_error_message(response)
            if provider_message:
                details["provider_message"] = provider_message
            if status_code >= 500:
                return (
                    f"LLM provider failed with HTTP {status_code}.",
                    details,
                )
            return (
                f"LLM request was rejected with HTTP {status_code}.",
                details,
            )
        return (
            "LLM request failed. Check connector URL, TLS settings, and API key.",
            details,
        )

    @staticmethod
    def _provider_error_message(response: Any) -> str | None:
        try:
            payload = response.json()
        except Exception:
            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()[:500]
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
        return None

    def _repair_final_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_content: str,
        validation_errors: list[str],
        response_schema: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> str:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "Repair the assistant output so it matches ChatFinalResponseContractV1. "
                    "Return only the strict JSON object. Do not call tools. Do not invent evidence."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "invalid_output": raw_content,
                        "validation_errors": validation_errors[:10],
                        "turn_context": state.build_turn_context_snapshot().model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        try:
            response = llm_client.responses_completion(
                messages=repair_messages,
                tools=[],
                response_schema=response_schema,
                tool_choice=None,
            )
        except Exception:
            self._logger.exception("llm final payload repair failed")
            return ""
        for usage in llm_client.collect_usages():
            state.llm_usages.append(usage)
        return response.output_text

    @staticmethod
    def _validate_final_payload(raw_content: str) -> tuple[dict[str, Any] | None, list[str]]:
        candidate = FinalPayloadJsonExtractor().extract(raw_content)
        try:
            parsed = json.loads(candidate.text)
        except json.JSONDecodeError as exc:
            return None, [f"root: invalid JSON payload ({exc.msg})"]
        if not isinstance(parsed, dict):
            return None, ["root: final payload must be a JSON object"]
        validated, errors = validate_final_response_payload(parsed)
        if validated is None:
            return None, errors
        return validated.to_payload(), []

    def _visualization_errors(
        self,
        *,
        payload: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> list[str]:
        visualization = payload.get("visualization")
        if visualization is None:
            return []
        if not isinstance(visualization, dict):
            return ["visualization: must be object or null"]
        execution = state.execution
        if execution is None:
            return ["visualization: execution result is required"]
        validated = self._visualization_validator.plan(
            execution=execution,
            llm_candidate=visualization,
        )
        if validated is None:
            return ["visualization: incompatible with execution preview"]
        payload["visualization"] = validated.model_dump(mode="json")
        return []

    @staticmethod
    def _tool_contracts(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        contracts: dict[str, dict[str, Any]] = {}
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            if isinstance(name, str) and isinstance(parameters, dict):
                contracts[name] = parameters
        return contracts

    @classmethod
    def _parse_and_validate_tool_arguments(
        cls,
        *,
        call: LlmFunctionCall,
        tool_contracts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        contract = tool_contracts.get(call.name)
        if contract is None:
            raise ToolArgumentContractError(f"unknown tool requested: {call.name}")
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ToolArgumentContractError(
                f"invalid JSON arguments for {call.name}: {exc.msg}"
            ) from exc
        if not isinstance(arguments, dict):
            raise ToolArgumentContractError(f"arguments for {call.name} must be an object")
        cls._validate_object_against_tool_schema(
            tool_name=call.name,
            arguments=arguments,
            schema=contract,
        )
        return arguments

    @staticmethod
    def _validate_object_against_tool_schema(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        schema: dict[str, Any],
    ) -> None:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        required_values = required if isinstance(required, list) else []
        for field_name in required_values:
            if isinstance(field_name, str) and field_name not in arguments:
                raise ToolArgumentContractError(f"{tool_name}.{field_name} is required")
        if schema.get("additionalProperties") is False:
            extra = set(arguments) - set(properties)
            if extra:
                raise ToolArgumentContractError(
                    f"{tool_name} received unsupported arguments: {sorted(extra)}"
                )
        for field_name, value in arguments.items():
            field_schema = properties.get(field_name)
            if isinstance(field_schema, dict):
                StrictResponsesRuntime._validate_json_type(
                    tool_name=tool_name,
                    field_name=field_name,
                    value=value,
                    field_schema=field_schema,
                )

    @staticmethod
    def _ensure_not_duplicate_tool_call(
        *,
        signature: tuple[str, str],
        seen_calls: set[tuple[str, str]],
    ) -> None:
        if signature in seen_calls:
            raise ToolArgumentContractError("duplicate function call")

    @staticmethod
    def _validate_json_type(
        *,
        tool_name: str,
        field_name: str,
        value: Any,
        field_schema: dict[str, Any],
    ) -> None:
        expected = field_schema.get("type")
        if expected is None:
            return
        type_names = expected if isinstance(expected, list) else [expected]
        if "null" in type_names and value is None:
            return
        validators = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, int | float) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
        }
        if not any(
            validators[type_name](value) for type_name in type_names if type_name in validators
        ):
            raise ToolArgumentContractError(f"{tool_name}.{field_name} has invalid type")
        enum_values = field_schema.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            raise ToolArgumentContractError(
                f"{tool_name}.{field_name} must be one of {enum_values}"
            )

    @staticmethod
    def _function_call_output(*, call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        }

    @staticmethod
    def _terminal_tool_contract_payload(
        *,
        input_items: list[dict[str, Any]],
        state: ToolOrchestrationState,
    ) -> dict[str, Any] | None:
        for item in input_items:
            raw_output = item.get("output")
            if not isinstance(raw_output, str):
                continue
            try:
                payload = json.loads(raw_output)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(payload, dict)
                and payload.get("error_code") == "llm_tool_contract_violation"
            ):
                return StrictResponsesRuntime._contract_error_payload(
                    state=state,
                    error_code="llm_tool_contract_violation",
                    message=str(payload.get("user_message") or "Tool contract violation."),
                )
            if isinstance(payload, dict) and payload.get("can_continue") is False:
                return StrictResponsesRuntime._contract_error_payload(
                    state=state,
                    error_code=str(payload.get("error_code") or "tool_execution_blocked"),
                    message=str(payload.get("user_message") or "Tool execution blocked."),
                )
        return None

    @staticmethod
    def _contract_error_payload(
        *,
        state: ToolOrchestrationState,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        state.add_warning(error_code)
        return {
            "answer": message,
            "answer_markdown": message,
            "sql": state.generated_sql,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [error_code],
            "clarification_request": None,
            "analysis_document": None,
            "visualization": None,
            "execution": state.execution.model_dump(mode="json")
            if state.execution is not None
            else None,
            "metadata_sources": sorted(state.metadata_sources),
            "context_tables": state.context_tables[:20],
            "optimization_trace": state.optimization_trace.model_dump(mode="json"),
            "warnings": list(state.warnings),
            "prompt_version": state.prompt_version,
        }

    @staticmethod
    def _summarize_tool_result(result: dict[str, Any]) -> str:
        if result.get("ok"):
            if "execution_id" in result:
                return f"Execution {result['execution_id']} completed"
            if "count" in result:
                return f"Returned {result['count']} items"
            return "Tool completed"
        return str(result.get("user_message") or result.get("error") or "Tool failed")

    @staticmethod
    def _emit_runtime_state(
        *,
        state: ToolOrchestrationState,
        record: Callable[[StreamEvent], None],
    ) -> None:
        analysis_plan = getattr(state, "analysis_plan", None)
        checkpoint = getattr(state, "checkpoint", None)
        worker_results = getattr(state, "worker_results", [])
        if analysis_plan is None and checkpoint is None and not worker_results:
            return
        record(
            RuntimeStateEvent(
                analysis_plan=analysis_plan.model_dump(mode="json")
                if analysis_plan is not None
                else None,
                worker_results=[
                    result.compact_payload()
                    for result in worker_results[-5:]
                    if hasattr(result, "compact_payload")
                ],
                checkpoint_digest=checkpoint.digest_payload() if checkpoint is not None else None,
            )
        )
