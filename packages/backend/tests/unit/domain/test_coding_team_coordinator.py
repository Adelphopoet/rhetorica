import asyncio
import json
from types import SimpleNamespace

from rhetorica_backend.contracts.coding import (
    CodingAgentTeamSnapshot,
    CodingCoordinatorDecision,
)
from rhetorica_backend.domain.coding.team_coordinator import (
    FINISH_TOOL_NAME,
    build_member_tool,
    build_team_coordinator,
    coordinator_instructions,
    finish_on_valid_decision,
)


def _team() -> CodingAgentTeamSnapshot:
    return CodingAgentTeamSnapshot.model_validate({
        "team_id": "team",
        "team_name": "dbt",
        "revision_id": "revision",
        "revision": 2,
        "coordinator_agent_profile_id": "coordinator",
        "coordinator_instruction": "Prefer small changes.",
        "members": [
            {
                "id": "dbt-engineer",
                "agent_profile_id": "developer",
                "label": "Data engineer",
                "instruction": "Check dbt changes.",
                "capability": "candidate",
            },
            {
                "id": "reviewer",
                "agent_profile_id": "reviewer",
                "label": "Reviewer",
                "capability": "review",
            },
        ],
    })


def test_sdk_manager_exposes_only_admitted_members_as_tools() -> None:
    team = _team()

    async def call(member, call_id, assignment):
        return {"status": "ok"}

    tools = [build_member_tool(member=member, call=call) for member in team.members]
    coordinator = build_team_coordinator(
        name="Coordinator",
        instructions=coordinator_instructions(team=team, structured_output=True),
        member_tools=tools,
        model="test-model",
        structured_output=True,
    )

    assert [tool.name for tool in coordinator.tools] == ["member_dbt-engineer", "member_reviewer"]
    assert coordinator.output_type is CodingCoordinatorDecision
    assert "Data engineer" in coordinator.tools[0].description
    assert "Check dbt changes." in coordinator.tools[0].description
    assert "Prefer small changes." in coordinator.instructions


def test_coordinator_without_structured_output_finishes_through_typed_tool() -> None:
    authorized: list[bool] = []
    coordinator = build_team_coordinator(
        name="Coordinator",
        instructions="Coordinate",
        member_tools=[],
        model="test-model",
        structured_output=False,
        authorize_finish=lambda: authorized.append(True),
    )
    finish = coordinator.tools[-1]

    output = asyncio.run(
        finish.on_invoke_tool(None, json.dumps({"decision": "stop", "summary": "Blocked."}))
    )

    assert finish.name == FINISH_TOOL_NAME
    assert coordinator.tool_use_behavior is finish_on_valid_decision
    assert json.loads(output) == {"decision": "stop", "summary": "Blocked."}
    assert authorized == [True]


def test_member_tool_forwards_call_id_and_assignment_without_extra_authority() -> None:
    member = _team().members[1]
    calls: list[tuple[str, str, str]] = []

    async def call(selected, call_id, assignment):
        calls.append((selected.id, call_id, assignment))
        return {"status": "failed", "reason": "Missing test"}

    tool = build_member_tool(member=member, call=call)
    output = asyncio.run(
        tool.on_invoke_tool(
            SimpleNamespace(tool_call_id="call-7"), json.dumps({"assignment": "Review abc"})
        )
    )
    invalid = asyncio.run(
        tool.on_invoke_tool(SimpleNamespace(tool_call_id="call-8"), json.dumps({"assignment": ""}))
    )

    assert calls == [("reviewer", "call-7", "Review abc")]
    assert json.loads(output) == {"status": "failed", "reason": "Missing test"}
    assert json.loads(invalid)["status"] == "invalid_arguments"
    assert set(tool.params_json_schema["properties"]) == {"assignment"}


def test_invalid_finish_returns_to_the_model_and_long_summary_is_bounded() -> None:
    finish = build_team_coordinator(
        name="Coordinator", instructions="-", member_tools=[], model="m", structured_output=False
    ).tools[-1]

    rejected = asyncio.run(finish.on_invoke_tool(None, json.dumps({"decision": "abort"})))
    accepted = asyncio.run(
        finish.on_invoke_tool(None, json.dumps({"decision": "stop", "summary": "x" * 3000}))
    )

    assert json.loads(rejected)["status"] == "invalid_arguments"
    as_result = lambda output: SimpleNamespace(tool=finish, output=output)  # noqa: E731
    assert finish_on_valid_decision(None, [as_result(rejected)]).is_final_output is False
    final = finish_on_valid_decision(None, [as_result(accepted)])
    assert final.is_final_output is True
    assert len(json.loads(final.final_output)["summary"]) == 2000
