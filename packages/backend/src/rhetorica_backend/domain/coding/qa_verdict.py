"""Strict QA completion for providers without structured response support."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agents import (
    FunctionTool,
    FunctionToolResult,
    ModelSettings,
    RunContextWrapper,
    ToolsToFinalOutputResult,
    default_tool_error_function,
)
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.strict_schema import ensure_strict_json_schema

QA_VERDICT_MISSING = "QA finished without calling submit_qa_verdict"


class QaResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["passed", "failed", "blocked"]
    reason: str


def accepted_verdict(output: Any) -> QaResult | None:
    try:
        return QaResult.model_validate_json(str(output))
    except ValidationError:
        return None


def finish_on_qa_verdict(
    _context: RunContextWrapper[Any], results: list[FunctionToolResult]
) -> ToolsToFinalOutputResult:
    """Stop on an accepted verdict; a rejected one returns to the model with its error."""

    for result in results:
        if result.tool.name == CodingQaVerdictTool.name and accepted_verdict(result.output):
            return ToolsToFinalOutputResult(is_final_output=True, final_output=result.output)
    return ToolsToFinalOutputResult(is_final_output=False)


def configure_unstructured_qa(agent: Any) -> None:
    """Every QA turn calls a tool, and the run stops on an accepted submit_qa_verdict."""

    settings = agent.model_settings if agent.model_settings is not None else ModelSettings()
    agent.model_settings = replace(settings, tool_choice="required", parallel_tool_calls=False)
    agent.reset_tool_choice = False
    agent.tool_use_behavior = finish_on_qa_verdict


async def extract_qa_verdict(result: Any) -> str:
    """The verdict is an accepted submit_qa_verdict output; a closing message is not a verdict."""

    verdict_calls = {
        item.raw_item.call_id
        for item in result.new_items
        if isinstance(item, ToolCallItem)
        and getattr(item.raw_item, "name", None) == CodingQaVerdictTool.name
    }
    for item in reversed(result.new_items):
        if isinstance(item, ToolCallOutputItem) and item.raw_item.get("call_id") in verdict_calls:
            verdict = accepted_verdict(item.output)
            if verdict is not None:
                return verdict.model_dump_json()
    return QaResult(status="blocked", reason=QA_VERDICT_MISSING).model_dump_json()


class CodingQaVerdictTool:
    name = "submit_qa_verdict"

    def __init__(self, authorize: Callable[[], None]):
        self._authorize = authorize

    def build(self) -> FunctionTool:
        async def invoke(context, arguments: str) -> str:
            self._authorize()
            try:
                return QaResult.model_validate_json(arguments).model_dump_json()
            except ValidationError as exc:
                return default_tool_error_function(context, exc)

        return FunctionTool(
            name=self.name,
            description="Finish independent QA after checking the exact candidate and running relevant tests. Submit passed, failed or blocked with a factual reason. Never claim unexecuted validation passed.",
            params_json_schema=ensure_strict_json_schema(QaResult.model_json_schema()),
            strict_json_schema=True,
            on_invoke_tool=invoke,
        )
