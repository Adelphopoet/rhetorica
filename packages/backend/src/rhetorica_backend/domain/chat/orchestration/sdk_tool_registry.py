"""SDK function registration and governed execution."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]
from referencing import Registry
from referencing.exceptions import Unresolvable

from agents import (
    Agent,
    FunctionTool,
    FunctionToolResult,
    ModelBehaviorError,
    ToolsToFinalOutputResult,
)
from agents.tool_context import ToolContext

from ..settings import get_chat_settings
from .agent_runtime import AgentRuntimeInput, AgentRuntimeTransport
from .final_response_contract import (
    SUBMIT_FINAL_ANSWER_TOOL_NAME,
    ChatFinalResponseContractV1,
    final_response_tool_schema,
)
from .models import ExecuteToolCallPlan, RejectRepeatedToolCallPlan, ToolCallRequest
from .sdk_runtime_contracts import AgentsSdkCapabilityError, _SdkTurnControl
from .tool_discovery import DISCOVER_SCHEMA, INVOKE_SCHEMA, ToolDiscoveryPolicy
from .tool_execution_gate import ToolExecutionGate
from .tool_output_policy import ToolOutputPolicy


class _FunctionToolRegistry:
    """Expose existing Rhetorica tool definitions as dynamically enabled SDK tools."""

    def __init__(
        self,
        *,
        coordinator: Any,
        runtime: AgentRuntimeInput[Any],
        transport: AgentRuntimeTransport[Any, Any],
        turn_control: _SdkTurnControl,
        explicit_final_answer_enabled: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self._runtime = runtime
        self._transport = transport
        self._turn_control = turn_control
        self._explicit_final_answer_enabled = explicit_final_answer_enabled
        self._initial_names = set(self._tool_names(runtime.tools))
        self._tools_by_name: dict[str, FunctionTool] = {}
        self._definitions_by_name: dict[str, dict[str, Any]] = {}
        self._results_by_call_id: dict[str, dict[str, Any]] = {}
        self._tool_names_by_call_id: dict[str, str] = {}
        self._current_model_turn = 0
        self._blocked_noted_turn: int | None = None
        self._gate = ToolExecutionGate(get_chat_settings().certified_tool_concurrency_enabled)
        self._dispatch = turn_control.tool_mode == "dispatch"
        self._dispatch_tools = [
            FunctionTool(
                name="discover_tools",
                description="Find exact tool IDs and schemas. Load groups or MCP tools via invoke_tool before invoking their members.",
                params_json_schema=DISCOVER_SCHEMA,
                strict_json_schema=False,
                is_enabled=self._dispatch_enabled,
                on_invoke_tool=self._discover,
            ),
            FunctionTool(
                name="invoke_tool",
                description="Invoke a loaded tool by exact discovered tool_id and arguments matching its original JSON Schema.",
                params_json_schema=INVOKE_SCHEMA,
                strict_json_schema=False,
                is_enabled=self._dispatch_enabled,
                on_invoke_tool=self._dispatch_invoke,
            ),
        ]
        if explicit_final_answer_enabled:
            self._register_final_answer_tool()
        self.refresh()

    @property
    def tools(self) -> list[FunctionTool]:
        if self._dispatch:
            return [
                *self._dispatch_tools,
                *[
                    tool
                    for name, tool in self._tools_by_name.items()
                    if name == SUBMIT_FINAL_ANSWER_TOOL_NAME
                ],
            ]
        return [self._tools_by_name[name] for name in sorted(self._tools_by_name)]

    def active_definitions(self) -> list[dict[str, Any]]:
        if self._dispatch:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.params_json_schema,
                    },
                }
                for tool in self.tools
                if self._turn_control.tools_enabled or tool.name == SUBMIT_FINAL_ANSWER_TOOL_NAME
            ]
        return [
            self._definitions_by_name[name]
            for name in sorted(self._definitions_by_name)
            if self._is_enabled(name)
        ]

    def begin_model_turn(self, iteration: int) -> None:
        self._current_model_turn = iteration

    def _dispatch_enabled(self, _context: Any, _agent: Any) -> bool:
        return self._turn_control.tools_enabled

    def tool_result(self, call_id: str | None) -> dict[str, Any] | None:
        if call_id is None:
            return None
        return self._results_by_call_id.get(call_id)

    def tool_name(self, call_id: str | None) -> str | None:
        if call_id is None:
            return None
        return self._tool_names_by_call_id.get(call_id)

    def refresh(self, agent: Agent[Any] | None = None) -> None:
        definitions = [
            *self._runtime.tools,
            *self._runtime.state.deferred_tool_definitions.values(),
            *self._runtime.state.loaded_tool_definitions.values(),
            *self._runtime.state.loaded_mcp_tool_definitions.values(),
        ]
        for definition in definitions:
            definition = json.loads(json.dumps(definition, sort_keys=True))
            parsed = self._parse_definition(definition)
            if parsed is None:
                continue
            name, description, parameters = parsed
            if name == SUBMIT_FINAL_ANSWER_TOOL_NAME:
                continue
            self._definitions_by_name[name] = definition
            if name in self._tools_by_name:
                self._tools_by_name[name].params_json_schema = parameters
                self._tools_by_name[name].description = description
                continue

            def is_enabled(
                _ctx: Any,
                _agent: Any,
                *,
                tool_name: str = name,
            ) -> bool:
                return self._is_enabled(tool_name)

            self._tools_by_name[name] = FunctionTool(
                name=name,
                description=description,
                params_json_schema=parameters,
                strict_json_schema=False,
                is_enabled=is_enabled,
                on_invoke_tool=self._invoke,
            )
        if agent is not None:
            agent.tools = cast(Any, self.tools)

    async def _discover(self, context: ToolContext[Any], arguments_json: str) -> str:
        if not self._turn_control.tools_enabled:
            return self._dispatch_result(
                context, "discover_tools", {"ok": False, "error_code": "finalization_required"}
            )
        try:
            arguments = json.loads(arguments_json)
            jsonschema.validate(arguments, DISCOVER_SCHEMA)
        except (ValueError, jsonschema.ValidationError):
            return self._dispatch_result(
                context,
                "discover_tools",
                {"ok": False, "error_code": "discovery_arguments_invalid"},
            )
        self.refresh()
        definitions = [
            definition
            for name, definition in sorted(self._definitions_by_name.items())
            if name in self._initial_names
            or name in self._runtime.state.deferred_tool_definitions
            or self._is_enabled(name)
        ]
        result = {"ok": True, **ToolDiscoveryPolicy().search(definitions, **arguments)}
        for entry in result["tools"]:
            name = entry["tool_id"]
            if entry.get("error") == "schema_exceeds_discovery_page_budget":
                entry["tool_artifact"] = self._runtime.state.tool_result_artifacts.register(
                    tool_name="discover_tools",
                    arguments={"tool_id": name},
                    result=self._definitions_by_name[name],
                )
                entry["next_action_hint"] = (
                    "Read schema fields from /function/parameters in this artifact."
                )
            entry["loaded"] = self._is_enabled(name)
            if not entry["loaded"]:
                entry["load_group"] = next(
                    (
                        group
                        for group, names in self._runtime.state.available_tool_groups.items()
                        if name in names
                    ),
                    None,
                )
        if not result["tools"]:
            result["next_action_hint"] = (
                "Use fewer keywords or an exact ID. Discover list_tool_groups or load_tool_group; "
                "load mcp_connectors before listing and loading remote MCP tools."
            )
        self._runtime.state.tool_call_count += 1
        self._runtime.state.record_executed_tool_call(
            tool_call_id=context.tool_call_id,
            tool_name="discover_tools",
            arguments=arguments,
            result=result,
            prompt_result=result,
        )
        return self._dispatch_result(context, "discover_tools", result)

    def _dispatch_result(self, context: ToolContext[Any], name: str, result: dict[str, Any]) -> str:
        self._results_by_call_id[context.tool_call_id] = result
        self._tool_names_by_call_id[context.tool_call_id] = name
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized) <= 16_000:
            return serialized
        prompt_result = self._coordinator._tool_result_prompt_view(
            tool_name=name, arguments={}, tool_result=result, state=self._runtime.state
        )
        envelope = self._coordinator._tool_result_prompt_serializer.serialize_envelope(
            tool_name=name, tool_result=prompt_result
        )
        return ToolOutputPolicy().apply(envelope, prompt_result).stable_json()

    async def _dispatch_invoke(self, context: ToolContext[Any], arguments_json: str) -> str:
        try:
            request = json.loads(arguments_json)
            jsonschema.validate(request, INVOKE_SCHEMA)
            name = request["tool_id"]
            definition = self._definitions_by_name.get(name)
            if (
                not definition
                or not self._is_enabled(name)
                or name == SUBMIT_FINAL_ANSWER_TOOL_NAME
            ):
                return self._dispatch_result(
                    context,
                    "invoke_tool",
                    {
                        "ok": False,
                        "error_code": "tool_not_loaded_or_permitted",
                        "next_action_hint": "Call discover_tools directly for this tool_id, then load its group before invoke_tool.",
                    },
                )
            schema = definition["function"]["parameters"]
            validator = jsonschema.validators.validator_for(schema)
            validator.check_schema(schema)
            validator(schema, registry=Registry()).validate(request["arguments"])
        except (ValueError, jsonschema.ValidationError):
            return self._dispatch_result(
                context, "invoke_tool", {"ok": False, "error_code": "tool_arguments_schema_invalid"}
            )
        except (jsonschema.SchemaError, Unresolvable):
            return self._dispatch_result(
                context, "invoke_tool", {"ok": False, "error_code": "tool_schema_unsupported"}
            )
        inner = copy.copy(context)
        inner.tool_name = name
        inner.tool_arguments = json.dumps(request["arguments"], ensure_ascii=False, sort_keys=True)
        return await self._invoke(inner, inner.tool_arguments)

    def _register_final_answer_tool(self) -> None:
        definition = final_response_tool_schema()
        parsed = self._parse_definition(definition)
        if parsed is None:
            raise AgentsSdkCapabilityError("Invalid final answer tool definition")
        name, description, parameters = parsed
        self._definitions_by_name[name] = definition
        self._tools_by_name[name] = FunctionTool(
            name=name,
            description=description,
            params_json_schema=parameters,
            strict_json_schema=True,
            is_enabled=True,
            on_invoke_tool=self._invoke_final_answer,
        )

    async def _invoke_final_answer(
        self,
        context: ToolContext[Any],
        arguments_json: str,
    ) -> str:
        call_id = context.tool_call_id or "call_missing_id"
        self._tool_names_by_call_id[call_id] = SUBMIT_FINAL_ANSWER_TOOL_NAME
        try:
            final_answer = ChatFinalResponseContractV1.model_validate_json(arguments_json)
        except Exception as exc:
            error_result = {
                "ok": False,
                "error_code": "llm_final_payload_invalid",
                "can_continue": True,
                "user_message": "submit_final_answer arguments violate the final contract.",
                "validation_error": str(exc)[:2_000],
            }
            self._results_by_call_id[call_id] = error_result
            return json.dumps(error_result, ensure_ascii=False, sort_keys=True)
        # The SDK receives the final contract; stream events receive tool status.
        self._results_by_call_id[call_id] = {"ok": True, "status": "succeeded"}
        return final_answer.model_dump_json()

    async def final_output_from_tools(
        self,
        _context: Any,
        tool_results: list[FunctionToolResult],
    ) -> ToolsToFinalOutputResult:
        for tool_result in tool_results:
            if tool_result.tool.name != SUBMIT_FINAL_ANSWER_TOOL_NAME:
                continue
            output = tool_result.output
            if not isinstance(output, str):
                return ToolsToFinalOutputResult(is_final_output=False)
            try:
                ChatFinalResponseContractV1.model_validate_json(output)
            except Exception:
                return ToolsToFinalOutputResult(is_final_output=False)
            return ToolsToFinalOutputResult(is_final_output=True, final_output=output)
        return ToolsToFinalOutputResult(is_final_output=False)

    async def _invoke(self, context: ToolContext[Any], arguments_json: str) -> str:
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            call_id = context.tool_call_id or "call_missing_id"
            error_result = {
                "ok": False,
                "error_code": "tool_arguments_json_invalid",
                "can_continue": True,
                "user_message": (
                    f"Tool {context.tool_name} received invalid JSON arguments. "
                    "Retry the call with one complete JSON object."
                ),
                "validation_error": exc.msg,
            }
            self._results_by_call_id[call_id] = error_result
            self._tool_names_by_call_id[call_id] = context.tool_name
            return json.dumps(error_result, ensure_ascii=False, sort_keys=True)
        if not isinstance(arguments, dict):
            raise ModelBehaviorError(f"Tool {context.tool_name} arguments must be a JSON object")

        async with self._gate.enter(context.tool_name):
            # Recheck after waiting: a prior invocation may have unloaded this tool.
            if not self._is_enabled(context.tool_name):
                return self._dispatch_result(
                    context,
                    context.tool_name,
                    {"ok": False, "error_code": "tool_not_loaded_or_permitted"},
                )
            request = ToolCallRequest(
                tool_name=context.tool_name,
                arguments=arguments,
                tool_call_id=context.tool_call_id,
            )
            args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
            execution_plan = self._coordinator._tool_execution_policy.plan_tool_call(
                state=self._runtime.state,
                tool_name=request.tool_name,
                args_json=args_json,
                tool_call_id=request.tool_call_id or "call_missing_id",
            )

            if isinstance(execution_plan, ExecuteToolCallPlan):
                self._runtime.state.tool_call_count += 1
                tool_result = await asyncio.to_thread(
                    self._runtime.tool_executor.execute_tool,
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    request=self._runtime.request,
                    db_connectors=self._runtime.db_connectors,
                    state=self._runtime.state,
                )
                self._runtime.state.tool_call_results[execution_plan.call_signature] = tool_result
                executed = True
            elif isinstance(execution_plan, RejectRepeatedToolCallPlan):
                tool_result = execution_plan.tool_result
                executed = False
                self._note_blocked_call()
            else:
                raise TypeError(f"Unsupported tool execution plan: {type(execution_plan).__name__}")
            call_id = request.tool_call_id or "call_missing_id"
            self._results_by_call_id[call_id] = tool_result
            self._tool_names_by_call_id[call_id] = request.tool_name

            prompt_tool_result = self._coordinator._tool_result_prompt_view(
                tool_name=request.tool_name,
                arguments=request.arguments,
                tool_result=tool_result,
                state=self._runtime.state,
            )
            if executed:
                self._runtime.state.record_executed_tool_call(
                    tool_call_id=request.tool_call_id or "call_missing_id",
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    result=tool_result,
                    prompt_result=prompt_tool_result,
                )
            prompt_artifact = self._coordinator._tool_result_prompt_serializer.serialize_envelope(
                tool_name=request.tool_name,
                tool_result=prompt_tool_result,
            )
            self._coordinator._log_prompt_artifact_diagnostics(prompt_artifact)
            for warning in prompt_artifact.warnings:
                self._runtime.state.add_warning(warning)

            self.refresh(context.agent if isinstance(context.agent, Agent) else None)
            return cast(str, prompt_artifact.stable_json())

    def _note_blocked_call(self) -> None:
        if self._blocked_noted_turn == self._current_model_turn:
            return
        self._blocked_noted_turn = self._current_model_turn
        self._runtime.state.blocked_tool_call_turn_count += 1
        if self._runtime.state.blocked_tool_call_turn_count >= 2:
            self._runtime.state.force_context_compaction = True

    def _is_enabled(self, name: str) -> bool:
        if name == SUBMIT_FINAL_ANSWER_TOOL_NAME:
            return self._explicit_final_answer_enabled
        return self._turn_control.tools_enabled and (
            name in self._initial_names
            or name in self._runtime.state.loaded_tool_definitions
            or name in self._runtime.state.loaded_mcp_tool_definitions
        )

    @staticmethod
    def _parse_definition(
        definition: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        if not isinstance(definition, dict) or definition.get("type") != "function":
            return None
        function = definition.get("function")
        if not isinstance(function, dict):
            return None
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            raise AgentsSdkCapabilityError("Invalid function tool definition")
        description = function.get("description")
        return name, description if isinstance(description, str) else "", parameters

    @staticmethod
    def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for definition in tools:
            parsed = _FunctionToolRegistry._parse_definition(definition)
            if parsed is not None:
                names.append(parsed[0])
        return names
