from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillBundle,
    WorkspaceSkillItem,
    WorkspaceSkillVersionBinding,
    WorkspaceSkillVersionSnapshot,
)
from rhetorica_backend.domain.chat.orchestration.tool_workspace_skill_capability_service import (
    ToolWorkspaceSkillCapabilityService,
)


class _Repository:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.allowed = True
        self.skill = WorkspaceSkillItem(
            id="skill-1",
            workspace_id="default",
            name="trino-guide",
            capability_family="chat_instruction",
            engine_scope="any",
            source_kind="manual_markdown",
            status="active",
            validation_status="valid",
            visible_to_default_assistant=True,
            content_markdown="# Main\n",
            content_sha256="main-digest",
            version_digest="version-1",
            created_at=now,
            updated_at=now,
        )
        self.version = WorkspaceSkillVersionSnapshot(
            skill_id="skill-1",
            workspace_id="default",
            version_digest="version-1",
            content_markdown="# Main\n",
            content_bundle=WorkspaceSkillBundle(
                assets=[
                    WorkspaceSkillAsset(path="SKILL.md", kind="main", content_markdown="# Main\n"),
                    WorkspaceSkillAsset(
                        path="rules/trino.md",
                        kind="rule",
                        content_markdown="0123456789",
                    ),
                ]
            ),
        )

    def get_active_chat_instruction(self, **_kwargs):
        return self.skill if self.allowed else None

    def get_version(self, *, version_digest: str, **_kwargs):
        return self.version if version_digest == self.version.version_digest else None


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        metadata_sources=set(),
        workspace_skills_read=[],
        workspace_skill_version_bindings=[],
        workspace_skill_read_addresses=[],
        add_warning=lambda _warning: None,
    )


def test_reader_returns_bounded_fragment_and_reuses_immutable_version() -> None:
    repository = _Repository()
    reader = ToolWorkspaceSkillCapabilityService(workspace_skill_repository=repository)
    state = _state()
    request = ChatAskRequest(question="read", workspace_id="default")

    first = reader.execute_read_workspace_skill(
        request=request,
        arguments={"skill_id": "skill-1", "path": "rules/trino.md", "max_chars": 4},
        state=state,
    )
    second = reader.execute_read_workspace_skill(
        request=request,
        arguments={
            "skill_id": "skill-1",
            "version_digest": "version-2",
            "path": "rules/trino.md",
            "offset": first["next_offset"],
            "max_chars": 4,
        },
        state=state,
    )

    assert first["content"] == "0123"
    assert first["truncated"] is True
    assert second["content"] == "4567"
    assert second["version_digest"] == "version-1"
    assert state.workspace_skills_read == [
        {
            "id": "skill-1",
            "name": "trino-guide",
            "title": None,
            "summary": None,
            "content_sha256": "main-digest",
            "version_digest": "version-1",
        }
    ]
    assert state.workspace_skill_version_bindings == [
        WorkspaceSkillVersionBinding(skill_id="skill-1", version_digest="version-1")
    ]
    assert state.workspace_skill_read_addresses == [
        {"skill_id": "skill-1", "version_digest": "version-1", "path": "rules/trino.md", "offset": 0, "length": 4},
        {"skill_id": "skill-1", "version_digest": "version-1", "path": "rules/trino.md", "offset": 4, "length": 4},
    ]


def test_reader_rechecks_live_access_and_rejects_traversal() -> None:
    repository = _Repository()
    reader = ToolWorkspaceSkillCapabilityService(workspace_skill_repository=repository)
    request = ChatAskRequest(question="read", workspace_id="default")

    traversal = reader.execute_read_workspace_skill(
        request=request,
        arguments={"skill_id": "skill-1", "path": "../secret"},
        state=_state(),
    )
    repository.allowed = False
    revoked = reader.execute_read_workspace_skill(
        request=request,
        arguments={"skill_id": "skill-1", "version_digest": "version-1"},
        state=_state(),
    )

    assert traversal["error_code"] == "workspace_skill_path_invalid"
    assert revoked["error_code"] == "workspace_skill_not_found"


def test_reader_keeps_first_turn_binding_when_live_skill_changes() -> None:
    repository = _Repository()
    reader = ToolWorkspaceSkillCapabilityService(workspace_skill_repository=repository)
    state = _state()
    request = ChatAskRequest(question="read", workspace_id="default")

    first = reader.execute_read_workspace_skill(
        request=request,
        arguments={"skill_id": "skill-1"},
        state=state,
    )
    repository.skill = repository.skill.model_copy(update={"version_digest": "version-2"})
    second = reader.execute_read_workspace_skill(
        request=request,
        arguments={"skill_id": "skill-1"},
        state=state,
    )

    assert first["version_digest"] == "version-1"
    assert second["version_digest"] == "version-1"


def test_reader_uses_previously_persisted_task_binding() -> None:
    repository = _Repository()
    repository.skill = repository.skill.model_copy(update={"version_digest": "version-2"})
    reader = ToolWorkspaceSkillCapabilityService(workspace_skill_repository=repository)
    state = _state()
    state.workspace_skill_version_bindings.append(
        WorkspaceSkillVersionBinding(skill_id="skill-1", version_digest="version-1")
    )

    result = reader.execute_read_workspace_skill(
        request=ChatAskRequest(question="read", workspace_id="default"),
        arguments={"skill_id": "skill-1"},
        state=state,
    )

    assert result["version_digest"] == "version-1"


def test_manifest_rejects_cursor_for_another_version() -> None:
    repository = _Repository()
    reader = ToolWorkspaceSkillCapabilityService(workspace_skill_repository=repository)
    request = ChatAskRequest(question="read", workspace_id="default")
    cursor = reader._encode_cursor("skill-1", "version-2", 0)

    result = reader.execute_list_workspace_skill_files(
        request=request,
        arguments={"skill_id": "skill-1", "version_digest": "version-1", "cursor": cursor},
        state=_state(),
    )

    assert result["error_code"] == "workspace_skill_cursor_version_mismatch"
