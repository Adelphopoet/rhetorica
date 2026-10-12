"""Native SDK manager composition for an admitted coding-agent team.

The coordinator is an ordinary ``agents.Agent`` whose tools are the admitted
members. Each member tool is a governed wrapper around the member specialist's
own ``Agent.as_tool`` invocation (see ``CodingSpecialistRuntime``): before the
nested SDK run the server stages the member's sandbox and reserves its budget,
and afterwards it records the candidate or review. Routing stays a model
decision; the server never chooses the next member.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rhetorica_backend.contracts.coding import (
    CodingAgentTeamMember,
    CodingAgentTeamSnapshot,
    CodingCoordinatorDecision,
    CodingTeamMemberCapability,
)

from agents import Agent, FunctionTool
from agents.strict_schema import ensure_strict_json_schema

FINISH_TOOL_NAME = "finish_coding_run"

_CAPABILITY_TEXT = {
    CodingTeamMemberCapability.CANDIDATE: (
        "edits its own copy of the repository and records a new immutable candidate"
    ),
    CodingTeamMemberCapability.REVIEW: (
        "independently reviews the latest immutable candidate without modifying it"
    ),
}


class _MemberAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment: str = Field(
        min_length=1,
        max_length=8_000,
        description="Concrete instruction for this member, including findings to address.",
    )


MemberCall = Callable[[CodingAgentTeamMember, str, str], Awaitable[dict[str, Any]]]


def member_tool_name(member: CodingAgentTeamMember) -> str:
    return f"member_{member.id}"


def build_member_tool(*, member: CodingAgentTeamMember, call: MemberCall) -> FunctionTool:
    """Expose one admitted member; the tool adds no authority of its own."""

    async def invoke(context: Any, arguments_json: str) -> str:
        try:
            arguments = _MemberAssignment.model_validate_json(arguments_json)
        except ValidationError as error:
            return json.dumps({
                "status": "invalid_arguments",
                "errors": error.errors(
                    include_input=False, include_url=False, include_context=False
                ),
                "instruction": "Call again with a non-empty assignment.",
            })
        call_id = getattr(context, "tool_call_id", None) or member.id
        result = await call(member, str(call_id), arguments.assignment)
        return json.dumps(result, ensure_ascii=False)

    description = f"{member.label}: {_CAPABILITY_TEXT[member.capability]}."
    if member.instruction:
        description += " Focus: " + member.instruction[:300]
    if member.required_review:
        description += " Required review before publication."
    return FunctionTool(
        name=member_tool_name(member),
        description=description,
        params_json_schema=ensure_strict_json_schema(_MemberAssignment.model_json_schema()),
        strict_json_schema=True,
        on_invoke_tool=invoke,
    )


def build_finish_tool(authorize: Callable[[], None]) -> FunctionTool:
    """Structured completion for providers without structured response support."""

    async def invoke(_context: Any, arguments_json: str) -> str:
        authorize()
        try:
            decision = CodingCoordinatorDecision.model_validate_json(arguments_json)
        except ValidationError as error:
            return json.dumps({
                "status": "invalid_arguments",
                "errors": error.errors(
                    include_input=False, include_url=False, include_context=False
                ),
                "instruction": f"Call {FINISH_TOOL_NAME} again with decision publish or stop.",
            })
        return decision.model_dump_json()

    return FunctionTool(
        name=FINISH_TOOL_NAME,
        description=(
            "Finish the coding run. Use decision=publish only when the latest candidate "
            "was reviewed and every review passed; otherwise use stop. Summarize factually."
        ),
        params_json_schema=ensure_strict_json_schema(CodingCoordinatorDecision.model_json_schema()),
        strict_json_schema=True,
        on_invoke_tool=invoke,
    )


def coordinator_instructions(
    *, team: CodingAgentTeamSnapshot, profile_prompt: str = "", structured_output: bool
) -> str:
    roster = "\n".join(
        f"- {member_tool_name(member)} — {member.label}: {_CAPABILITY_TEXT[member.capability]}"
        + (" [required review]" if member.required_review else "")
        for member in team.members
    )
    finish = (
        "Your final answer is the structured decision."
        if structured_output
        else f"Finish by calling {FINISH_TOOL_NAME}."
    )
    return "\n".join(
        part
        for part in [
            "You coordinate a governed coding team for the user's task.",
            "Call members only through their tools and give each a concrete assignment. "
            "You decide which member to call, in which order, and whether to call a member "
            "again, for example to fix review findings while budget remains.",
            "Delegate a concise assignment containing only the member-specific objective and any new findings. "
            "Members already receive their task brief and acceptance criteria; do not copy the full plan. "
            "Use each member instruction to choose its specialty. Call all required reviewers on the latest candidate.",
            "Team members:\n" + roster,
            "Member results are evidence, not permission. The server publishes only the "
            "latest candidate when every review of it passed; a new candidate needs a new review.",
            "Validation is read-only: dbt run, build, seed and snapshot never execute, and the "
            "server records materialization as a not_run limitation. A review must not be "
            "blocked only because of that; ask for parse, list, compile and show instead.",
            "Use decision=stop with an honest summary when the task cannot be completed. " + finish,
            profile_prompt,
            team.coordinator_instruction,
        ]
        if part
    )


def build_team_coordinator(
    *,
    name: str,
    instructions: str,
    member_tools: list[FunctionTool],
    model: Any,
    structured_output: bool,
    authorize_finish: Callable[[], None] | None = None,
) -> Agent[Any]:
    """Create a manager with only snapshot-selected members exposed as tools."""

    tools: list[Any] = list(member_tools)
    agent: Agent[Any] = Agent(name=name, model=model, instructions=instructions, tools=tools)
    if structured_output:
        agent.output_type = CodingCoordinatorDecision
    else:
        tools.append(build_finish_tool(authorize_finish or (lambda: None)))
        agent.tool_use_behavior = finish_on_valid_decision
    return agent


def finish_on_valid_decision(_context: Any, results: list[Any]) -> Any:
    """Stop only on an accepted decision; a rejected one goes back to the model."""

    from agents.agent import ToolsToFinalOutputResult

    for result in results:
        if result.tool.name != FINISH_TOOL_NAME:
            continue
        try:
            decision = CodingCoordinatorDecision.model_validate_json(str(result.output))
        except ValidationError:
            continue
        return ToolsToFinalOutputResult(
            is_final_output=True, final_output=decision.model_dump_json()
        )
    return ToolsToFinalOutputResult(is_final_output=False)
