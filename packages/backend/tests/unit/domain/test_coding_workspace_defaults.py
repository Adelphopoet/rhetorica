from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rhetorica_backend.contracts.coding import (
    CodingResourceSelection,
    UpsertWorkspaceCodingDefaults,
)
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.resources import CodingResourcePolicy
from rhetorica_backend.domain.coding.workspace_defaults import CodingWorkspaceDefaultsService


def selection():
    return CodingResourceSelection(
        root_agent_id="root",
        developer_agent_id="dev",
        qa_agent_id="qa",
        gitlab_connector_id="gitlab",
        project_id="group/project",
        target_branch="main",
        environment_profile_id="env",
    )


@pytest.mark.parametrize(
    "role,admin,allowed",
    [
        ("owner", False, True),
        ("analyst", False, False),
        ("viewer", False, False),
        ("analyst", True, True),
    ],
)
def test_only_owner_or_admin_can_replace_workspace_defaults(role, admin, allowed):
    service = Mock()
    service.require_actor.return_value = SimpleNamespace(is_admin=admin)
    service.workspaces.membership.return_value = SimpleNamespace(role=role)
    repository = Mock()
    defaults = CodingWorkspaceDefaultsService(service=service, repository=repository)
    user = SimpleNamespace(id="user", workspace_id="workspace")
    request = UpsertWorkspaceCodingDefaults(expected_revision=0, resources=selection())
    if allowed:
        defaults.replace(user, request)
        service.validate_resources.assert_called_once()
        repository.replace.assert_called_once_with(
            workspace_id="workspace", actor_user_id="user", request=request
        )
    else:
        with pytest.raises(CodingRunError, match="workspace_owner_access_denied"):
            defaults.replace(user, request)
        repository.replace.assert_not_called()


def test_workspace_defaults_choose_explicit_root_and_never_expand_allowlist():
    service = Mock()
    service.workspaces.membership.return_value = SimpleNamespace(role="analyst")
    service.users.get_by_id.return_value = Mock(is_active=True)
    service.policies.get_agent_policy.return_value = SimpleNamespace(
        allowed_subagent_ids=["dev", "qa"]
    )
    service.agents.get.side_effect = lambda **k: SimpleNamespace(
        id=k["agent_id"],
        title=k["agent_id"],
        allowed_gitlab_connector_names=["gitlab"],
        allowed_database_connector_names=[],
    )
    service.gitlab_connectors.list.return_value = [
        SimpleNamespace(id="gitlab", name="gitlab", is_active=True)
    ]
    service.databases.list.return_value = []
    service.environments.list_latest.return_value = [SimpleNamespace(id="env", revision=1)]
    service.environments.get.return_value = SimpleNamespace(id="env")
    service.workspace_defaults.get.return_value = SimpleNamespace(revision=3, resources=selection())
    policy = CodingResourcePolicy(
        users=service.users,
        workspaces=service.workspaces,
        agents=service.agents,
        policies=service.policies,
        gitlab_connectors=service.gitlab_connectors,
        databases=service.databases,
        environments=service.environments,
        enabled=True,
        workspace_defaults=service.workspace_defaults,
    )
    user = SimpleNamespace(id="user", workspace_id="workspace")
    result = policy.capabilities(user)
    assert result["root_agent_id"] == "root"
    assert result["launch_defaults"]["developer_agent_id"] == "dev"
    assert [a["id"] for a in result["specialists"]] == ["dev", "qa"]
    service.policies.get_agent_policy.return_value = SimpleNamespace(allowed_subagent_ids=[])
    result = policy.capabilities(user, root_agent_id="other")
    assert result["specialists"] == []
    assert result["launch_defaults"] is None
    assert result["defaults_unavailable_reason"] == "coding_delegation_not_allowed"
    service.workspace_defaults.get.assert_called_with("workspace")


@pytest.mark.parametrize("role", ["develop", "qa"])
def test_dependency_failure_precedes_specialist_and_qa_checks(role):
    import asyncio
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    from rhetorica_backend.domain.coding.executor import GovernedCodingExecutor

    executor = object.__new__(GovernedCodingExecutor)
    executor.runner = Mock()
    executor.runner.prepare_environment.side_effect = ValueError("coding_dependency_install_failed")
    executor.agents = SimpleNamespace(run=AsyncMock())
    executor._artifact = Mock(return_value=b"snapshot")
    executor._tasks = Mock(
        return_value=[
            SimpleNamespace(id="coordinator", role="coordinator", checkpoint_ref="base"),
            SimpleNamespace(id="developer", role="developer"),
        ]
    )
    executor._persist = Mock()
    executor._producer_task_id = Mock(return_value="developer")
    admission = SimpleNamespace(
        run=SimpleNamespace(candidate=None),
        environment_profile=SimpleNamespace(
            id="env", revision=2, pip_dependencies=["pytest==8.3.5"]
        ),
    )
    kwargs = {
        "admission": admission,
        "task": SimpleNamespace(id="task"),
        "operation_id": "operation",
        "deadline": SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(seconds=30)),
    }
    if role == "qa":
        kwargs["candidate"] = SimpleNamespace(artifact_id="candidate")
    with pytest.raises(ValueError, match="coding_dependency_install_failed"):
        asyncio.run(getattr(executor, role)(**kwargs))
    executor.runner.prepare_environment.assert_called_once()
    executor.agents.run.assert_not_called()
    executor._persist.assert_not_called()


def test_preparation_receipt_is_durable_and_included_in_task_artifacts():
    import asyncio
    import io
    import json
    import tarfile
    from datetime import UTC, datetime, timedelta

    from rhetorica_backend.domain.coding.executor import GovernedCodingExecutor

    executor = object.__new__(GovernedCodingExecutor)
    executor.runner = Mock()
    receipt = {
        "status": "prepared",
        "resolved_dependencies": ["pytest==8.3.5"],
        "profile_revision": 2,
    }
    executor.runner.prepare_environment.return_value = receipt
    executor.service = Mock()
    executor._persist = Mock(return_value="prepared-artifact")
    admission = SimpleNamespace(
        run=SimpleNamespace(id="run"),
        environment_profile=SimpleNamespace(
            id="env", revision=2, pip_dependencies=["pytest==8.3.5"]
        ),
    )
    result = asyncio.run(
        executor._prepare_environment(
            admission,
            SimpleNamespace(id="task"),
            SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(seconds=60)),
        )
    )
    assert result == ("prepared-artifact",)
    executor.service.journal.put.assert_called_once_with(
        run_id="run", task_id="task", operation_id="python-environment:env:2", value=receipt
    )
    archive = executor._persist.call_args.args[2]
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        assert json.load(bundle.extractfile("python-environment.json")) == receipt


def test_pip_preparation_respects_expired_deadline_without_installation():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted
    from rhetorica_backend.domain.coding.executor import GovernedCodingExecutor

    executor = object.__new__(GovernedCodingExecutor)
    executor.runner = Mock()
    admission = SimpleNamespace(
        environment_profile=SimpleNamespace(
            id="env", revision=1, pip_dependencies=["pytest==8.3.5"]
        )
    )
    with pytest.raises(CodingBudgetExhausted):
        asyncio.run(
            executor._prepare_environment(
                admission,
                SimpleNamespace(id="task"),
                SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            )
        )
    executor.runner.prepare_environment.assert_not_called()


@pytest.mark.parametrize("role", ["develop", "qa"])
def test_install_failure_finishes_activity_without_retrying_specialist(role):
    import asyncio
    from unittest.mock import AsyncMock

    from rhetorica_backend.contracts.coding import CodingReasonCode
    from rhetorica_backend.domain.coding.activities import CodingActivities
    from rhetorica_backend.domain.coding.errors import CodingEnvironmentPreparationFailed

    lifecycle = Mock()
    getattr(lifecycle, role).side_effect = None
    setattr(lifecycle, role, AsyncMock(side_effect=CodingEnvironmentPreparationFailed()))
    lifecycle.fail_after_activity_error = AsyncMock(
        return_value=SimpleNamespace(
            status="failed", reason_code=CodingReasonCode.DEPENDENCY_INSTALL_FAILED
        )
    )
    result = asyncio.run(getattr(CodingActivities(lifecycle), role)({"run_id": "run", "cycle": 1}))
    assert result["terminal"] is True
    assert result["reason_code"] == "coding_dependency_install_failed"
    lifecycle.fail_after_activity_error.assert_awaited_once_with(
        run_id="run", reason_code=CodingReasonCode.DEPENDENCY_INSTALL_FAILED
    )
