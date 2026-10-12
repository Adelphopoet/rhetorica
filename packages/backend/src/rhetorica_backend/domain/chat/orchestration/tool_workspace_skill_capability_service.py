"""Workspace skill orchestration tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolWorkspaceSkillCapabilityService:
    """Capability-family service for on-demand workspace instruction skills."""

    def __init__(
        self,
        *,
        workspace_skill_repository: WorkspaceSkillRepository | None,
    ) -> None:
        self._workspace_skill_repository = workspace_skill_repository

    def execute_read_workspace_skill(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        if self._workspace_skill_repository is None:
            state.add_warning("workspace_skill_repository_missing")
            return build_tool_error(
                error_code="workspace_skill_repository_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Workspace skills are not configured.",
                retry_recommended=False,
                can_continue=True,
            )

        skill_id = arguments.get("skill_id")
        skill_name = arguments.get("skill_name")
        if not isinstance(skill_id, str):
            skill_id = None
        if not isinstance(skill_name, str):
            skill_name = None
        if not (skill_id and skill_id.strip()) and not (skill_name and skill_name.strip()):
            return build_tool_error(
                error_code="workspace_skill_identifier_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Provide skill_id or skill_name.",
                retry_recommended=False,
                can_continue=True,
            )

        if request.agent_id is not None:
            allowed_skill_ids = request.agent_allowed_workspace_skill_ids or []
            default_visible = None
        else:
            allowed_skill_ids = None
            default_visible = True
        skill = self._workspace_skill_repository.get_active_chat_instruction(
            workspace_id=request.workspace_id,
            skill_id=skill_id,
            skill_name=skill_name,
            skill_ids=allowed_skill_ids,
            visible_to_default_assistant=default_visible,
        )
        if skill is None:
            state.add_warning("workspace_skill_not_found")
            return build_tool_error(
                error_code="workspace_skill_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Workspace skill was not found, inactive, or not allowed for this agent.",
                retry_recommended=False,
                can_continue=True,
            )

        state.metadata_sources.add("workspace_skill")
        snapshot = {
            "id": skill.id,
            "name": skill.name,
            "title": skill.title,
            "summary": skill.summary,
            "content_sha256": skill.content_sha256,
            "content_markdown": skill.content_markdown,
        }
        existing_ids = {
            str(item.get("id"))
            for item in state.workspace_skills_read
            if isinstance(item, dict)
        }
        if skill.id not in existing_ids:
            state.workspace_skills_read.append(snapshot)
        return {
            "ok": True,
            "skill_id": skill.id,
            "skill_name": skill.name,
            "title": skill.title,
            "summary": skill.summary,
            "content_markdown": skill.content_markdown,
            "content_sha256": skill.content_sha256,
        }
