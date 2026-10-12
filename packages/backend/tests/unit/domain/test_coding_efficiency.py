import asyncio
import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.coding import (
    CodingAgentTeamMember,
    CodingAgentTeamRevision,
    CodingPlanDraft,
    CodingTeamMemberCapability,
    ExecutionPrincipal,
)
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillVersionBinding,
)
from rhetorica_backend.domain.coding.agents import (
    CodingSpecialistAgentFactory,
    _WorkspaceSkillReadInput,
)
from rhetorica_backend.domain.coding.instructions import encode_instructions, project_instructions
from rhetorica_backend.domain.coding.publication_decision import missing_required_reviews
from rhetorica_backend.domain.coding.service import CodingRunService
from rhetorica_backend.domain.coding.skill_manifest import bound_skill_manifest
from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer


def test_instructions_project_only_member_brief_and_shared_acceptance():
    plan = CodingPlanDraft(
        task="Goal",
        steps=["Stage one"],
        developer_brief="Write SQL",
        qa_brief="Check SQL",
        acceptance_criteria=["Returns one"],
    )
    raw = encode_instructions(plan.instruction(), plan).decode()
    candidate = project_instructions(raw, CodingTeamMemberCapability.CANDIDATE)
    review = project_instructions(raw, CodingTeamMemberCapability.REVIEW)
    assert "Write SQL" in candidate and "Check SQL" not in candidate
    assert "Check SQL" in review and "Write SQL" not in review
    assert "Returns one" in candidate and "Returns one" in review
    assert "Stage one" not in candidate
    assert project_instructions("Legacy task") == "Legacy task"


def test_required_review_uses_member_identity_not_role_label():
    member = CodingAgentTeamMember(
        id="sql-expert",
        agent_profile_id="specialist",
        label="Anything",
        capability="review",
        required_review=True,
    )
    run = SimpleNamespace(team_snapshot=SimpleNamespace(members=[member]))
    report = SimpleNamespace(
        checks=[SimpleNamespace(name="independent_review:other", status="passed")]
    )
    assert missing_required_reviews(run, report) == ("independent_review:sql-expert",)
    report.checks.append(SimpleNamespace(name="independent_review:sql-expert", status="passed"))
    assert missing_required_reviews(run, report) == ()
    report.checks[-1].status = "failed"
    assert missing_required_reviews(run, report)
    report.checks[-1].status = "passed"
    run.team_snapshot.members.append(member.model_copy(update={"id": "performance"}))
    assert missing_required_reviews(run, report) == ("independent_review:performance",)


def test_candidate_cannot_be_required_reviewer():
    with pytest.raises(ValidationError, match="required_review_capability"):
        CodingAgentTeamRevision(
            id="r",
            team_id="t",
            workspace_id="w",
            revision=1,
            coordinator_agent_profile_id="c",
            members=[
                {
                    "id": "m",
                    "agent_profile_id": "p",
                    "label": "Reviewer",
                    "capability": "candidate",
                    "required_review": True,
                }
            ],
            created_at="2026-10-05T00:00:00Z",
        )


def test_skill_discovery_excludes_reference_inventory():
    assets = [
        WorkspaceSkillAsset(
            path="SKILL.md", kind="main", frontmatter={"name": "dbt", "description": "Build models"}
        ),
        WorkspaceSkillAsset(path="references/long.md", kind="doc", content_markdown="x" * 9000),
    ]
    repo = SimpleNamespace(
        get_version=lambda **kwargs: SimpleNamespace(
            content_bundle=WorkspaceSkillBundle(assets=assets)
        )
    )
    task = SimpleNamespace(
        workspace_id="w",
        skill_version_bindings=[WorkspaceSkillVersionBinding(skill_id="s", version_digest="v")],
    )
    catalog = bound_skill_manifest(repository=repo, task=task)
    assert catalog[0]["instructions"] == [
        {"name": "dbt", "description": "Build models", "path": "SKILL.md"}
    ]
    assert "references/long.md" not in json.dumps(catalog)


def test_skill_page_is_not_truncated_again_by_tool_serializer():
    content = "Полная инструкция. " * 900
    gateway = SimpleNamespace(
        invoke=AsyncMock(return_value={"content": content, "truncated": False, "next_offset": None})
    )
    factory = CodingSpecialistAgentFactory(authorizer=Mock(), gateway=gateway)
    principal = ExecutionPrincipal(
        actor_user_id="u", workspace_id="w", entry_source="web_chat", run_id="r"
    )
    agent = factory.build(
        capability=CodingTeamMemberCapability.REVIEW,
        principal=principal,
        model="test",
        timeout_seconds=5,
    )
    skill = next(t for t in agent.tools if t.name == "workspace_skill_read")
    result = json.loads(
        asyncio.run(
            skill.on_invoke_tool(
                SimpleNamespace(tool_call_id="call"),
                _WorkspaceSkillReadInput(
                    skill_id="s", version_digest="v", path="SKILL.md"
                ).model_dump_json(),
            )
        )
    )
    assert result["content"] == content and not result["truncated"]
    assert "repository_apply_patch" not in [t.name for t in agent.tools]
    assert "candidate_diff" in [t.name for t in agent.tools]


def test_dbt_list_keeps_structured_nodes_and_receipt():
    receipt = {
        "command": "list",
        "status": "passed",
        "evidence_scope": "candidate",
        "id": "receipt",
    }
    raw = {
        "operation": "run_dbt",
        "exit_code": 0,
        "stdout": '{"unique_id":"model.a","columns":{"ok":{}}}',
        "dbt_receipt": receipt,
        "stderr": "",
        "offset": None,
    }
    result = json.loads(CodingToolOutputSerializer().serialize(raw, call_id="call"))
    assert result["selected_nodes"][0]["unique_id"] == "model.a"
    assert result["dbt_receipt"] == receipt
    assert "stdout" not in result and "stderr" not in result
    assert raw["stdout"]  # receipt remains untouched


def _archive(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return out.getvalue()


def test_candidate_diff_uses_base_and_candidate_and_supports_paging():
    service = object.__new__(CodingRunService)
    candidate = SimpleNamespace(
        artifact_id="candidate", digest="digest", base_sha="base", changed_paths=["model.sql"]
    )
    view = SimpleNamespace(
        run=SimpleNamespace(candidate=candidate),
        progress=SimpleNamespace(
            tasks=[SimpleNamespace(role="coordinator", id="coordinator", checkpoint_ref="base")]
        ),
    )
    service.view = Mock(return_value=view)
    archives = {
        "base": _archive({"model.sql": "select 1\n"}),
        "candidate": _archive({"model.sql": "select 2\n"}),
    }
    service.artifacts = SimpleNamespace(
        scope=lambda _: {"task_id": "writer"},
        download_owned=lambda **kw: (archives[kw["artifact_id"]], None),
    )
    user = SimpleNamespace(workspace_id="w", id="u")
    full = service.diff("run", user)
    assert "-select 1" in full["diff"] and "+select 2" in full["diff"]
    page = service.diff("run", user, path="model.sql", max_chars=10)
    assert page["truncated"] and page["next_offset"] == 10
    assert page["candidate_digest"] == "digest"
    assert (
        page["diff"] + service.diff("run", user, path="model.sql", offset=10)["diff"]
        == full["diff"]
    )


def test_verbose_dbt_output_preserves_evidence_and_stays_bounded():
    from rhetorica_backend.domain.chat.llm.token_budget import LlmTokenEstimator

    result = {
        "exit_code": 0,
        "stdout": "x" * 30000,
        "dbt_receipt": {
            "id": "receipt",
            "command": "compile",
            "status": "passed",
            "evidence_scope": "candidate",
        },
    }
    output = CodingToolOutputSerializer().serialize(result, call_id="call")
    assert json.loads(output)["dbt_receipt"]["status"] == "passed"
    assert LlmTokenEstimator(None).count_text(output) <= 4000
    historic = CodingToolOutputSerializer(max_tokens=256).serialize(result, call_id="call")
    assert LlmTokenEstimator(None).count_text(historic) <= 256


def test_bad_candidate_diff_path_is_recoverable_without_reading_artifacts():
    from datetime import UTC, datetime, timedelta

    from rhetorica_backend.domain.coding.agents import _CandidateDiffInput
    from rhetorica_backend.domain.coding.runtime_tools import CodingRuntimeToolGateway

    candidate = SimpleNamespace(digest="digest", changed_paths=["models/a.sql"])
    run = SimpleNamespace(
        id="r", status="running", owner_user_id="u", workspace_id="w", candidate=candidate
    )
    service = SimpleNamespace(
        runs=SimpleNamespace(get=lambda **_: run),
        users=SimpleNamespace(
            get_by_id=lambda **_: SimpleNamespace(for_workspace=lambda _: "user")
        ),
        resources=SimpleNamespace(validate_run=Mock()),
        journal=None,
        diff=Mock(),
    )
    gateway = CodingRuntimeToolGateway(
        runner=Mock(),
        skills=Mock(),
        service=service,
        settings=Mock(),
        candidate=candidate,
        deadline=SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=1)),
    )
    result = asyncio.run(
        gateway._invoke(
            name="candidate_diff",
            task=SimpleNamespace(run_id="r", workspace_id="w", owner_user_id="u"),
            principal=Mock(),
            arguments=_CandidateDiffInput(path="models"),
        )
    )
    assert result["status"] == "invalid_arguments"
    assert result["changed_paths"] == ["models/a.sql"]
    service.diff.assert_not_called()


def test_oversize_patch_is_rejected_before_runner_call():
    from rhetorica_backend.contracts.coding import CODING_OPERATION_ARGUMENT_MAX_CHARS
    from rhetorica_backend.domain.coding.agents import _RepositoryPatchInput
    from rhetorica_coding_runner.contracts import ExecuteRequest

    value = "x" * (CODING_OPERATION_ARGUMENT_MAX_CHARS + 1)
    with pytest.raises(ValidationError):
        _RepositoryPatchInput(patch=value)
    with pytest.raises(ValidationError):
        ExecuteRequest(operation="apply_patch", arguments={"patch": value}, timeout_seconds=30)
    accepted = _RepositoryPatchInput(patch=value[:-1])
    ExecuteRequest(operation="apply_patch", arguments=accepted.model_dump(), timeout_seconds=30)
