import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from rhetorica_backend.contracts.coding import ExecutionPrincipal, SandboxExecutionResult
from rhetorica_backend.domain.coding.agents import _ValidateDbtInput
from rhetorica_backend.domain.coding.dbt import DbtCommandPolicy
from rhetorica_backend.domain.coding.runtime_tools import CodingRuntimeToolGateway


@pytest.fixture
def suite(monkeypatch):
    """Use the real gateway, request validation and receipts; fake only external I/O."""
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    run = SimpleNamespace(
        id="r",
        status="running",
        owner_user_id="u",
        workspace_id="w",
        data_warehouse=SimpleNamespace(connector_id="trino"),
    )
    receipts = {}
    journal = SimpleNamespace(
        get=lambda **kw: receipts.get(kw["operation_id"]),
        put=lambda **kw: receipts.__setitem__(kw["operation_id"], kw["value"]),
    )
    service = SimpleNamespace(
        runs=SimpleNamespace(get=lambda **_: run, record_validation_evidence=Mock()),
        users=SimpleNamespace(
            get_by_id=lambda **_: SimpleNamespace(for_workspace=lambda _: "user")
        ),
        resources=SimpleNamespace(validate_run=Mock()),
        journal=journal,
    )
    http_client = httpx.AsyncClient

    def profile(request):
        assert request.url.path == "/internal/v1/dbt-profiles"
        return httpx.Response(
            200,
            json={
                "runtime_profile": {
                    "profile_name": "test",
                    "profiles_yml": "test: {}",
                    "expires_at": deadline.isoformat(),
                }
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: http_client(**kw, transport=httpx.MockTransport(profile))
    )
    calls = []
    failure = {"index": None, "incomplete": False}

    def execute(task_id, request):
        assert task_id == "t"
        dbt = request.dbt_request
        calls.append(dbt)
        failed = len(calls) == failure["index"]
        incomplete = failed and failure["incomplete"]
        exit_code = 124 if incomplete else 1 if failed else 0
        return SandboxExecutionResult(
            operation="run_dbt",
            exit_code=exit_code,
            duration_ms=1,
            dbt_receipt=DbtCommandPolicy().receipt(
                request=dbt,
                exit_code=exit_code,
                duration_ms=1,
                stdout="",
                incomplete=incomplete,
            ),
        )

    gateway = CodingRuntimeToolGateway(
        runner=SimpleNamespace(execute=execute),
        skills=Mock(),
        service=service,
        settings=SimpleNamespace(
            coding_query_gateway_url="http://gateway", coding_query_gateway_internal_token="test"
        ),
        deadline=SimpleNamespace(expires_at=deadline),
        candidate=SimpleNamespace(digest="a" * 64),
    )
    task = SimpleNamespace(id="t", run_id="r", workspace_id="w", owner_user_id="u")
    principal = ExecutionPrincipal(
        actor_user_id="u", workspace_id="w", entry_source="web_chat", run_id="r", task_id="t"
    )

    def invoke(selectors):
        args = _ValidateDbtInput(project_subdir="dbt", selectors=selectors)
        args._call_id = "suite"
        return asyncio.run(
            gateway.invoke(name="validate_dbt", task=task, principal=principal, arguments=args)
        )

    return SimpleNamespace(
        invoke=invoke,
        calls=calls,
        receipts=receipts,
        failure=failure,
        evidence=service.runs.record_validation_evidence,
    )


@pytest.mark.parametrize("selectors", [["model_a"], ["model_a", "model_b"]])
def test_suite_shows_each_selector_with_separate_receipt_and_replays(suite, selectors):
    result = suite.invoke(selectors)
    assert [r.command for r in suite.calls] == ["parse", "list", "compile"] + ["show"] * len(
        selectors
    )
    assert [r.selectors for r in suite.calls] == [[], selectors, selectors] + [
        [s] for s in selectors
    ]
    assert suite.calls[2].no_introspect and suite.calls[2].no_populate_cache
    assert all(r.show_limit == 5 and not r.no_introspect for r in suite.calls[3:])
    assert result["status"] == "passed" and result["completed"]
    assert result["materialization_status"] == "not_run"
    checks = result["checks"]
    assert len({c["dbt_receipt"]["id"] for c in checks}) == len(checks)
    assert len({c["tool_result"]["call_id"] for c in checks}) == len(checks)
    for check in checks:
        key = f"tool:{check['tool_result']['call_id']}"
        assert suite.receipts[key]["dbt_receipt"] == check["dbt_receipt"]
    assert suite.evidence.call_count == len(checks)
    # Replaying individual steps must also work after losing the suite-level receipt.
    suite.receipts.pop("tool:suite")
    assert suite.invoke(selectors) == result
    assert len(suite.calls) == len(checks)
    assert suite.evidence.call_count == len(checks)


@pytest.mark.parametrize("fail_at", [1, 3, 4, 5])
@pytest.mark.parametrize("incomplete", [False, True])
def test_suite_stops_on_failed_or_incomplete_command(suite, fail_at, incomplete):
    suite.failure.update(index=fail_at, incomplete=incomplete)
    result = suite.invoke(["model_a", "model_b"])
    assert len(suite.calls) == fail_at
    assert result["status"] == ("blocked" if incomplete else "failed")
    assert result["completed"] is (fail_at == 5 and not incomplete)


def test_rejected_final_show_is_not_a_completed_command(suite):
    # Empty selectors normalize away in DbtRunRequest, so the final show is rejected.
    result = suite.invoke(["model_a", " "])
    assert [r.command for r in suite.calls] == ["parse", "list", "compile", "show"]
    assert result["checks"][-1]["status"] == "invalid_arguments"
    assert result["status"] == "failed" and not result["completed"]
    assert suite.evidence.call_count == 4
