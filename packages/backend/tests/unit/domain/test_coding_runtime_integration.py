import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rhetorica_backend.contracts.coding import CodingTeamMemberCapability
from rhetorica_backend.domain.coding.commands import CodingCommandPolicy
from rhetorica_backend.domain.coding.errors import CodingToolAccessDenied
from rhetorica_backend.domain.coding.model import CodingBudgetModel
from rhetorica_backend.domain.coding.templates import coding_profile_templates

from agents.items import ModelResponse
from agents.usage import Usage


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "push"],
        ["git", "diff", "--output=outside"],
        ["find", ".", "-exec", "curl"],
        ["rg", "--pre=curl", "pattern"],
        ["python", "-c", "print(1)"],
        ["cat", "../../secret"],
        ["cat", "/etc/passwd"],
    ],
)
def test_offline_command_boundary_denies_escape_and_external_mutations(argv):
    with pytest.raises(CodingToolAccessDenied):
        CodingCommandPolicy().validate(argv)


def test_templates_never_grant_resource_access_or_publication():
    profiles = coding_profile_templates(workspace_id="owned")
    assert len(profiles) == 3
    assert all(
        not p.allowed_gitlab_connector_names and not p.allowed_mcp_connector_names for p in profiles
    )


def test_completed_model_receipt_replays_after_worker_restart_without_new_request():
    delegate = SimpleNamespace(
        get_response=AsyncMock(
            return_value=ModelResponse(output=[], usage=Usage(requests=1), response_id="response")
        )
    )
    expires = datetime.now(UTC) + timedelta(minutes=1)
    runs = SimpleNamespace(
        reserve_iteration=Mock(return_value=SimpleNamespace(deadline=expires)),
        record_model_usage=Mock(),
    )
    task = SimpleNamespace(
        id="task",
        run_id="run",
        workspace_id="workspace",
        owner_user_id="owner",
        agent_profile_id="dev",
    )
    receipts = {}
    journal = SimpleNamespace(
        get=lambda **s: receipts.get(tuple(s.values())),
        put=lambda value, **s: receipts.update({tuple(s.values()): value}),
    )
    args = {
        "runs": runs,
        "task": task,
        "operation_id": "develop-1",
        "deadline": SimpleNamespace(expires_at=expires),
        "journal": journal,
    }
    first = asyncio.run(CodingBudgetModel(delegate, **args).get_response(input="same prompt"))
    restarted = asyncio.run(CodingBudgetModel(delegate, **args).get_response(input="same prompt"))
    assert first.response_id == restarted.response_id == "response"
    delegate.get_response.assert_awaited_once()
    runs.reserve_iteration.assert_called_once()


def test_every_sdk_role_builds_with_strict_tool_schemas():
    from rhetorica_backend.contracts.coding import ExecutionPrincipal
    from rhetorica_backend.domain.coding.agents import CodingSpecialistAgentFactory

    factory = CodingSpecialistAgentFactory(authorizer=Mock(), gateway=Mock())
    principal = ExecutionPrincipal(
        actor_user_id="owner", workspace_id="workspace", entry_source="web_chat", run_id="run"
    )
    for capability in [CodingTeamMemberCapability.CANDIDATE, CodingTeamMemberCapability.REVIEW]:
        agent = factory.build(
            capability=capability, principal=principal, model="test-model", timeout_seconds=10
        )
        assert all(tool.strict_json_schema for tool in agent.tools)


def test_worker_workflow_imports_pass_temporal_determinism_validation():
    from rhetorica_backend.domain.coding.temporal_workflow import CodingWorkflow
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    async def validate():
        SandboxedWorkflowRunner().prepare_workflow(
            workflow._Definition.must_from_class(CodingWorkflow)
        )

    asyncio.run(validate())


def test_model_request_identity_ignores_sdk_callback_identity():
    import json

    from rhetorica_backend.domain.coding.model import _request_value

    from agents import FunctionTool

    async def callback(context, arguments):
        return "ok"

    async def restarted_callback(context, arguments):
        return "ok"

    first = FunctionTool(
        name="read",
        description="read",
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=callback,
    )
    restarted = FunctionTool(
        name="read",
        description="read",
        params_json_schema=first.params_json_schema,
        on_invoke_tool=restarted_callback,
    )
    assert json.dumps(first, default=_request_value, sort_keys=True) == json.dumps(
        restarted, default=_request_value, sort_keys=True
    )


def test_subsequent_model_invocations_fit_persisted_postgres_key_limit():
    delegate = SimpleNamespace(
        get_response=AsyncMock(
            return_value=ModelResponse(output=[], usage=Usage(requests=1), response_id="response")
        )
    )
    expires = datetime.now(UTC) + timedelta(minutes=1)
    runs = SimpleNamespace(
        reserve_iteration=Mock(return_value=SimpleNamespace(deadline=expires)),
        record_model_usage=Mock(),
    )
    task = SimpleNamespace(
        id="t" * 36,
        run_id="r" * 36,
        workspace_id="workspace",
        owner_user_id="owner",
        agent_profile_id="dev",
    )

    async def execute():
        model = CodingBudgetModel(
            delegate,
            runs=runs,
            task=task,
            operation_id="developer:0",
            deadline=SimpleNamespace(expires_at=expires),
        )
        await model.get_response(input="first")
        await model.get_response(input="second")

    asyncio.run(execute())
    assert runs.reserve_iteration.call_count == 2
    assert all(len(c.kwargs["invocation_id"]) <= 128 for c in runs.reserve_iteration.call_args_list)


def test_runner_read_receipt_retains_pagination_metadata():
    from rhetorica_backend.contracts.coding import SandboxExecutionResult

    result = SandboxExecutionResult.model_validate({
        "operation": "read_file",
        "exit_code": 0,
        "stdout": "read",
        "duration_ms": 1,
        "offset": 0,
        "next_offset": 4,
        "total_chars": 10,
    })
    assert result.next_offset == 4 and result.total_chars == 10


def test_completed_sdk_turn_retries_candidate_transport_without_another_model_call():
    from rhetorica_backend.domain.coding.specialist_runtime import CodingSpecialistRuntime

    executor = object.__new__(CodingSpecialistRuntime)
    user = SimpleNamespace(for_workspace=Mock(return_value="live-user"))
    executor.service = SimpleNamespace(
        users=SimpleNamespace(get_by_id=Mock(return_value=user)),
        require_actor=Mock(),
        agent=Mock(),
        resources=SimpleNamespace(validate_run=Mock()),
        journal=SimpleNamespace(get=Mock(return_value={"output": "completed developer summary"})),
    )
    executor.llms = Mock()
    run = SimpleNamespace(id="run", owner_user_id="owner", workspace_id="workspace")
    task = SimpleNamespace(id="task", agent_profile_id="developer")
    result = asyncio.run(executor.run(SimpleNamespace(run=run), task, "developer:0", None, "task"))
    assert result == "completed developer summary"
    executor.llms.get_connection.assert_not_called()
    executor.service.resources.validate_run.assert_called_once_with(run, "live-user")


def test_denied_command_does_not_execute_and_allows_a_subsequent_valid_command():
    from rhetorica_backend.contracts.coding import SandboxExecutionResult
    from rhetorica_backend.domain.coding.agents import _RepositoryCommandInput
    from rhetorica_backend.domain.coding.runtime_tools import CodingRuntimeToolGateway

    runner = SimpleNamespace(
        execute=Mock(
            return_value=SandboxExecutionResult(
                operation="run_command",
                exit_code=0,
                stdout="clean",
                duration_ms=1,
            )
        )
    )
    service = SimpleNamespace(
        runs=SimpleNamespace(
            get=Mock(
                return_value=SimpleNamespace(
                    status="running", owner_user_id="owner", workspace_id="workspace"
                )
            )
        ),
        resources=SimpleNamespace(validate_run=Mock()),
        users=SimpleNamespace(
            get_by_id=Mock(return_value=SimpleNamespace(for_workspace=lambda _: "user"))
        ),
        journal=None,
    )
    gateway = CodingRuntimeToolGateway(
        runner=runner,
        service=service,
        skills=None,
        settings=None,
        deadline=SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=1)),
    )
    task = SimpleNamespace(run_id="run", id="task", workspace_id="workspace", owner_user_id="owner")

    async def exercise():
        denied = await gateway.invoke(
            name="repository_run_command",
            task=task,
            principal=None,
            arguments=_RepositoryCommandInput(argv=["git", "push"]),
        )
        assert denied == {"status": "denied", "reason_code": "coding_git_command_not_allowed"}
        runner.execute.assert_not_called()
        result = await gateway.invoke(
            name="repository_run_command",
            task=task,
            principal=None,
            arguments=_RepositoryCommandInput(argv=["git", "status"]),
        )
        assert result["stdout"] == "clean"

    asyncio.run(exercise())
    assert service.resources.validate_run.call_count == 2


def test_large_tool_outputs_have_a_hard_budget_and_recoverable_full_receipt():
    import json

    from rhetorica_backend.domain.chat.llm.token_budget import LlmTokenEstimator
    from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer

    serializer = CodingToolOutputSerializer()
    raw = {
        "operation": "run_command",
        "exit_code": 1,
        "stdout": '"large log"\n' * 100000,
        "stderr": "actual error at the end",
    }
    serialized = serializer.serialize(raw, call_id="call-42")
    value = json.loads(serialized)
    assert LlmTokenEstimator(None).count_text(serialized) <= 4000
    assert value["exit_code"] == 1 and value["prompt_truncated"] is True
    assert value["tool_result"]["call_id"] == "call-42"
    assert "actual error" in value["preview_head"] + value["preview_tail"]
    assert raw["stdout"].endswith('"large log"\n')


def test_missing_provider_usage_is_not_reported_as_measured_zero():
    from rhetorica_backend.domain.coding.usage import model_usage

    missing = ModelResponse(output=[], usage=Usage(), response_id=None)
    assert model_usage(missing)["usage_reported"] is False
    reported = ModelResponse(
        output=[],
        usage=Usage(),
        response_id=None,
        raw_usage={"input_tokens": 0, "output_tokens": 0},
    )
    assert model_usage(reported)["usage_reported"] is True


def test_native_sdk_tool_loop_does_not_inject_megabyte_outputs():
    import json

    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
    from rhetorica_backend.contracts.coding import ExecutionPrincipal
    from rhetorica_backend.domain.coding.agents import CodingSpecialistAgentFactory

    from agents import RunConfig, Runner
    from agents.models.interface import Model

    observed = []

    class ScriptedModel(Model):
        async def get_response(self, *args, **kwargs):
            observed.append(len(json.dumps(kwargs["input"])))
            index = len(observed)
            output = [
                ResponseFunctionToolCall(
                    type="function_call",
                    call_id=f"call-{index}",
                    name="repository_run_command",
                    arguments='{"argv":["git","diff"]}',
                )
            ]
            if index == 6:
                output = [
                    ResponseOutputMessage(
                        id="final",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(type="output_text", text="done", annotations=[])
                        ],
                    )
                ]
            return ModelResponse(output=output, usage=Usage(), response_id=None)

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("not streaming")
            yield

    gateway = SimpleNamespace(
        invoke=AsyncMock(
            return_value={"operation": "run_command", "exit_code": 0, "stdout": "x" * 1_000_000}
        )
    )
    factory = CodingSpecialistAgentFactory(authorizer=Mock(), gateway=gateway)
    agent = factory.build(
        capability=CodingTeamMemberCapability.CANDIDATE,
        principal=ExecutionPrincipal(
            actor_user_id="owner", workspace_id="workspace", entry_source="web_chat", run_id="run"
        ),
        model=ScriptedModel(),
        timeout_seconds=10,
    )
    asyncio.run(
        Runner.run(agent, "inspect diff", max_turns=6, run_config=RunConfig(tracing_disabled=True))
    )
    assert len(observed) == 6
    # Five million-character tool results stay below 50k transport characters combined.
    assert observed[-1] < 50_000
    assert all(after - before < 10_000 for before, after in pairwise(observed))


@pytest.mark.parametrize(
    "restore,exit_code,expected_calls", [(False, 0, 0), (True, 0, 1), (True, 1, 0)]
)
def test_lost_sandbox_replays_successful_patches_without_changing_cached_receipts(
    restore, exit_code, expected_calls
):
    from rhetorica_backend.contracts.coding import SandboxExecutionResult
    from rhetorica_backend.domain.coding.agents import _RepositoryPatchInput
    from rhetorica_backend.domain.coding.runtime_tools import CodingRuntimeToolGateway

    receipt = {"operation": "apply_patch", "exit_code": exit_code, "duration_ms": 17}
    service = SimpleNamespace(
        runs=SimpleNamespace(
            get=Mock(
                return_value=SimpleNamespace(
                    status="running", owner_user_id="owner", workspace_id="workspace"
                )
            )
        ),
        resources=SimpleNamespace(validate_run=Mock()),
        users=SimpleNamespace(
            get_by_id=Mock(return_value=SimpleNamespace(for_workspace=lambda _: "user"))
        ),
        journal=SimpleNamespace(get=Mock(return_value=receipt), put=Mock()),
    )
    runner = SimpleNamespace(
        execute=Mock(
            return_value=SandboxExecutionResult(
                operation="apply_patch", exit_code=0, duration_ms=99
            )
        )
    )
    gateway = CodingRuntimeToolGateway(
        runner=runner,
        service=service,
        skills=None,
        settings=None,
        deadline=SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=1)),
        restore_mutations=restore,
    )
    arguments = _RepositoryPatchInput(patch="diff")
    arguments._call_id = "patch"
    task = SimpleNamespace(run_id="run", id="task", workspace_id="workspace", owner_user_id="owner")
    result = asyncio.run(
        gateway.invoke(
            name="repository_apply_patch", task=task, principal=None, arguments=arguments
        )
    )
    assert result == receipt
    assert runner.execute.call_count == expected_calls
    service.resources.validate_run.assert_called_once()


def test_completed_agent_restores_checkpoint_after_sandbox_loss_without_new_model_request():
    from rhetorica_backend.domain.coding.specialist_runtime import CodingSpecialistRuntime

    runtime = object.__new__(CodingSpecialistRuntime)
    runtime.runner = SimpleNamespace(close=Mock(), stage=Mock())
    runtime.llms = Mock()
    runtime.service = SimpleNamespace(
        users=SimpleNamespace(
            get_by_id=Mock(return_value=SimpleNamespace(for_workspace=lambda _: "user"))
        ),
        require_actor=Mock(),
        agent=Mock(),
        resources=SimpleNamespace(validate_run=Mock()),
        journal=SimpleNamespace(
            get=Mock(return_value={"output": "done", "snapshot_ref": "snapshot"})
        ),
        artifacts=SimpleNamespace(download_owned=Mock(return_value=(b"candidate", None))),
    )
    run = SimpleNamespace(id="run", owner_user_id="owner", workspace_id="workspace")
    task = SimpleNamespace(id="task", agent_profile_id="developer")
    result = asyncio.run(
        runtime.run(
            SimpleNamespace(run=run), task, "developer:0", None, "task", restore_mutations=True
        )
    )
    assert result == "done"
    runtime.runner.stage.assert_called_once_with("task", b"candidate")
    runtime.llms.get_connection.assert_not_called()


def test_historical_output_preview_preserves_file_content_instead_of_json_wrapper():
    import json

    from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer

    serializer = CodingToolOutputSerializer(max_tokens=256)
    value = json.loads(
        serializer.serialize(
            {
                "operation": "read_file",
                "exit_code": 0,
                "stdout": "CRITICAL CONFIG\n" + "x" * 5000 + "\nEND OF FILE",
                "stderr": "",
                "dbt_receipt": None,
                "offset": 0,
                "next_offset": 5031,
                "total_chars": 5031,
                "duration_ms": 1,
                "truncated": False,
            },
            call_id="real-receipt",
        )
    )
    assert "CRITICAL CONFIG" in value["preview_head"]
    assert "END OF FILE" in value["preview_tail"]
    assert len(json.dumps(value, separators=(",", ":"))) <= 512
