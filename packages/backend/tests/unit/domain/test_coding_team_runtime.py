"""SDK coordinator turn: native tool routing, durable recovery and live authorization."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.contracts.coding import CodingAgentTeamSnapshot
from rhetorica_backend.domain.coding import team_runtime
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.team_runtime import CodingTeamCoordinatorRuntime

from agents.testing import ScriptedModel, function_call

TEAM = CodingAgentTeamSnapshot.model_validate({
    "team_id": "team",
    "team_name": "dbt",
    "revision_id": "revision",
    "revision": 1,
    "coordinator_agent_profile_id": "coordinator",
    "members": [
        {
            "id": "engineer",
            "agent_profile_id": "dev",
            "label": "Engineer",
            "capability": "candidate",
        },
        {"id": "reviewer", "agent_profile_id": "qa", "label": "Reviewer", "capability": "review"},
    ],
})


class Journal:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], dict] = {}

    def get(self, *, run_id, task_id, operation_id):
        return self.values.get((run_id, task_id, operation_id))

    def put(self, *, run_id, task_id, operation_id, value):
        self.values[(run_id, task_id, operation_id)] = value


def _fixture(monkeypatch, steps, *, validate_run=None):
    journal = Journal()
    model_holder: dict[str, ScriptedModel] = {}

    def build_sdk_model(*, connection, openai_client):
        model_holder["model"] = ScriptedModel(steps)
        return model_holder["model"]

    monkeypatch.setattr(team_runtime.sdk_models, "build_sdk_model", build_sdk_model)

    class SessionStore:
        def __init__(self, *args, **kwargs):
            pass

        def for_task(self, task_id):
            return None

        def begin_operation(self, task_id, operation_id):
            return None

    monkeypatch.setattr(team_runtime, "PostgresAgentSessionStore", SessionStore)
    tasks = [
        SimpleNamespace(id="engineer-task", team_member_id="engineer", role="developer"),
        SimpleNamespace(id="reviewer-task", team_member_id="reviewer", role="qa"),
    ]
    runs = SimpleNamespace(
        tasks=Mock(return_value=tasks),
        record_chat_event=Mock(),
        reserve_iteration=Mock(
            return_value=SimpleNamespace(deadline=datetime.now(UTC) + timedelta(minutes=5))
        ),
        record_model_usage=Mock(),
    )
    user = SimpleNamespace(for_workspace=lambda workspace_id: "live-user")
    service = SimpleNamespace(
        journal=journal,
        runs=runs,
        users=SimpleNamespace(get_by_id=lambda user_id: user),
        require_actor=Mock(),
        resources=SimpleNamespace(validate_run=validate_run or Mock()),
        agent=Mock(return_value=SimpleNamespace(title="Coordinator", admin_prompt="")),
        artifacts=SimpleNamespace(download_owned=Mock(return_value=(b"Add net_revenue", "d"))),
    )
    connection = LlmConnectionConfig(
        base_url="http://llm.invalid/v1",
        model="test",
        endpoint_path="/chat/completions",
        extra_settings={"structured_output_enabled": False},
    )
    runtime = CodingTeamCoordinatorRuntime(
        service=service,
        specialists=SimpleNamespace(connection=Mock(return_value=connection)),
        db=None,
    )
    run = SimpleNamespace(
        id="run",
        workspace_id="workspace",
        owner_user_id="owner",
        team_snapshot=TEAM,
        repository=SimpleNamespace(project_id="group/dbt", target_branch="main", base_sha="b" * 40),
        data_warehouse=None,
    )
    admission = SimpleNamespace(
        run=run, principal=SimpleNamespace(model_copy=lambda update: SimpleNamespace(**update))
    )
    task = SimpleNamespace(
        id="coordinator-task",
        run_id="run",
        workspace_id="workspace",
        owner_user_id="owner",
        agent_profile_id="coordinator",
        instruction_artifact_id="artifact://instructions",
        max_iterations=10,
    )
    deadline = SimpleNamespace(
        timeout_seconds=60, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    return runtime, admission, task, deadline, journal, model_holder, service


SCRIPT = [
    [function_call("member_engineer", {"assignment": "Add the column"}, call_id="call-1")],
    [function_call("member_reviewer", {"assignment": "Check fct_orders"}, call_id="call-2")],
    [
        function_call(
            "finish_coding_run",
            {"decision": "publish", "summary": "Reviewed candidate."},
            call_id="call-3",
        )
    ],
]


def test_coordinator_routes_members_and_recovers_from_journal(monkeypatch) -> None:
    runtime, admission, task, deadline, journal, holder, service = _fixture(monkeypatch, SCRIPT)
    calls: list[tuple[str, str, str]] = []

    async def invoke(member_id, call_id, assignment):
        calls.append((member_id, call_id, assignment))
        return {"status": "candidate_recorded" if member_id == "engineer" else "passed"}

    decision = asyncio.run(
        runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke)
    )

    assert decision.decision == "publish"
    assert calls == [
        ("engineer", "call-1", "Add the column"),
        ("reviewer", "call-2", "Check fct_orders"),
    ]
    first_request = holder["model"].first_call
    assert {tool.name for tool in first_request.tools} == {
        "member_engineer",
        "member_reviewer",
        "finish_coding_run",
    }
    events = [call.kwargs["event_type"] for call in service.runs.record_chat_event.call_args_list]
    assert events.count("team.member_started") == 2
    assert events[-1] == "team.coordinator_decision"

    # A retried activity replays journaled model responses and member results.
    journal.values.pop(("run", "coordinator-task", "coordinator-result:coordinator:0"))
    replayed = asyncio.run(
        runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke)
    )
    assert replayed == decision
    assert len(calls) == 2


def test_revoked_grant_before_member_call_stops_the_turn(monkeypatch) -> None:
    validate = Mock(
        side_effect=[None, CodingRunError("coding_delegation_not_allowed", status_code=403)]
    )
    runtime, admission, task, deadline, *_ = _fixture(monkeypatch, SCRIPT, validate_run=validate)

    async def invoke(member_id, call_id, assignment):
        raise AssertionError("a revoked member must not run")

    with pytest.raises(CodingRunError, match="coding_delegation_not_allowed"):
        asyncio.run(runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke))


def test_member_result_is_returned_to_the_coordinator_model(monkeypatch) -> None:
    runtime, admission, task, deadline, _, holder, _ = _fixture(monkeypatch, SCRIPT)

    async def invoke(member_id, call_id, assignment):
        return (
            {"status": "failed", "reason": "refunds missing"}
            if member_id == "reviewer"
            else {"status": "candidate_recorded"}
        )

    asyncio.run(runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke))

    last_input = holder["model"].last_call.input
    outputs = [
        json.loads(item["output"])
        for item in last_input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert {"status": "failed", "reason": "refunds missing"} in outputs


def test_long_cyrillic_assignment_fits_the_event_log(monkeypatch) -> None:
    from rhetorica_backend.infra.postgres.coding_run_repository import _bounded_event_payload

    assignment = "Добавь колонку net_revenue и проверь возвраты. " * 80
    script = [
        [function_call("member_engineer", {"assignment": assignment}, call_id="call-1")],
        [
            function_call(
                "finish_coding_run", {"decision": "stop", "summary": "Итог " * 600}, call_id="c2"
            )
        ],
    ]
    runtime, admission, task, deadline, _, _, service = _fixture(monkeypatch, script)
    service.runs.record_chat_event.side_effect = lambda **kwargs: _bounded_event_payload({
        **kwargs["payload"],
        "event_key": kwargs["event_key"],
    })

    received: list[str] = []

    async def invoke(member_id, call_id, assignment):
        received.append(assignment)
        return {"status": "candidate_recorded", "summary": "Готово. " * 600}

    decision = asyncio.run(
        runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke)
    )

    assert decision.decision == "stop"
    # Only the event log is shortened; the member receives the whole assignment.
    assert received == [assignment]
    started = next(
        call.kwargs["payload"]
        for call in service.runs.record_chat_event.call_args_list
        if call.kwargs["event_type"] == "team.member_started"
    )
    assert started["assignment_truncated"] is True
    assert assignment.startswith(started["assignment"])


def test_repeat_review_returns_the_stored_full_report_without_a_model_run(monkeypatch) -> None:
    script = [
        [function_call("member_reviewer", {"assignment": "Review"}, call_id="call-1")],
        [function_call("member_reviewer", {"assignment": "Clarify"}, call_id="call-2")],
        [function_call("finish_coding_run", {"decision": "stop", "summary": "-"}, call_id="c3")],
    ]
    runtime, admission, task, deadline, _, holder, _ = _fixture(monkeypatch, script)
    full = "Подробные замечания ревьюера. " * 200
    responses = [
        {"status": "failed", "candidate_digest": "a" * 64, "reason": full},
        {"status": "failed", "candidate_digest": "a" * 64, "reason": full[:512], "repeat": True},
    ]

    async def invoke(member_id, call_id, assignment):
        return responses.pop(0)

    asyncio.run(runtime.run(admission, task, "coordinator:0", deadline, invoke_member=invoke))

    outputs = [
        json.loads(item["output"])
        for item in holder["model"].last_call.input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert outputs[1]["reason"] == full
