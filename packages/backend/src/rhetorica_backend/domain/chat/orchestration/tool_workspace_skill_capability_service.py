"""Bounded, version-pinned workspace skill tools."""

from __future__ import annotations

import base64
import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillAsset,
    WorkspaceSkillItem,
    WorkspaceSkillVersionBinding,
    WorkspaceSkillVersionSnapshot,
)
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolWorkspaceSkillCapabilityService:
    """Reads only authorized, persisted fragments of immutable skill versions."""

    _MAX_CHARS = 4_000
    _MAX_MANIFEST_ENTRIES = 32

    def __init__(self, *, workspace_skill_repository: WorkspaceSkillRepository | None) -> None:
        self._workspace_skill_repository = workspace_skill_repository

    def execute_list_workspace_skill_files(
        self, *, request: ChatAskRequest, arguments: dict[str, Any], state: ToolOrchestrationState
    ) -> dict[str, Any]:
        skill, error = self._resolve_live_skill(request=request, arguments=arguments, state=state)
        if error is not None:
            return error
        if skill is None:
            return self._error("workspace_skill_not_found", "Workspace skill was not found.")
        cursor, cursor_error = self._decode_cursor(arguments.get("cursor"))
        if cursor_error is not None:
            return cursor_error
        digest = self._requested_digest(
            skill=skill,
            arguments=arguments,
            cursor=cursor,
            state=state,
        )
        if cursor is not None and (
            cursor["skill_id"] != skill.id or cursor["version_digest"] != digest
        ):
            return self._error(
                "workspace_skill_cursor_version_mismatch",
                "Cursor belongs to another skill version.",
            )
        version, version_error = self._resolve_version(skill=skill, digest=digest)
        if version_error is not None:
            return version_error
        if version is None:
            return self._error(
                "workspace_skill_version_not_found", "The selected skill version is unavailable."
            )
        if not self._still_live(request=request, arguments=arguments, skill_id=skill.id, state=state):
            return self._error(
                "workspace_skill_not_found",
                "Workspace skill was not found, inactive, or not allowed for this agent.",
            )
        start = cursor["offset"] if cursor is not None else 0
        if start > len(version.content_bundle.assets):
            return self._error(
                "workspace_skill_cursor_invalid", "Cursor is outside the skill manifest."
            )
        page = version.content_bundle.assets[start : start + self._MAX_MANIFEST_ENTRIES]
        next_offset = start + len(page)
        next_cursor = (
            self._encode_cursor(skill.id, version.version_digest, next_offset)
            if next_offset < len(version.content_bundle.assets)
            else None
        )
        self._record_binding(state=state, skill=skill, version=version)
        return {
            "ok": True,
            "skill_id": skill.id,
            "skill_name": skill.name,
            "version_digest": version.version_digest,
            "entries": [
                {"path": asset.path, "kind": asset.kind, "size_chars": len(asset.content_markdown)}
                for asset in page
            ],
            "total_entries": len(version.content_bundle.assets),
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }

    def execute_read_workspace_skill(
        self, *, request: ChatAskRequest, arguments: dict[str, Any], state: ToolOrchestrationState
    ) -> dict[str, Any]:
        skill, error = self._resolve_live_skill(request=request, arguments=arguments, state=state)
        if error is not None:
            return error
        if skill is None:
            return self._error("workspace_skill_not_found", "Workspace skill was not found.")
        version, version_error = self._resolve_version(
            skill=skill,
            digest=self._requested_digest(
                skill=skill,
                arguments=arguments,
                cursor=None,
                state=state,
            ),
        )
        if version_error is not None:
            return version_error
        if version is None:
            return self._error(
                "workspace_skill_version_not_found", "The selected skill version is unavailable."
            )
        if not self._still_live(request=request, arguments=arguments, skill_id=skill.id, state=state):
            return self._error(
                "workspace_skill_not_found",
                "Workspace skill was not found, inactive, or not allowed for this agent.",
            )
        asset, path_error = self._resolve_asset(version=version, raw_path=arguments.get("path"))
        if path_error is not None:
            return path_error
        if asset is None:
            return self._error("workspace_skill_path_not_found", "Skill has no readable files.")
        offset = arguments.get("offset", 0)
        max_chars = arguments.get("max_chars", self._MAX_CHARS)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return self._error(
                "workspace_skill_offset_invalid", "Offset must be a non-negative integer."
            )
        if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
            return self._error(
                "workspace_skill_max_chars_invalid", "max_chars must be a positive integer."
            )
        if offset > len(asset.content_markdown):
            return self._error(
                "workspace_skill_offset_invalid", "Offset is outside the selected skill file."
            )
        content = asset.content_markdown[offset : offset + min(max_chars, self._MAX_CHARS)]
        next_offset = offset + len(content)
        truncated = next_offset < len(asset.content_markdown)
        self._record_binding(
            state=state,
            skill=skill,
            version=version,
            read_address={
                "path": asset.path,
                "offset": offset,
                "length": len(content),
            },
        )
        return {
            "ok": True,
            "skill_id": skill.id,
            "skill_name": skill.name,
            "title": skill.title,
            "summary": skill.summary,
            "version_digest": version.version_digest,
            "content_sha256": skill.content_sha256,
            "path": asset.path,
            "content": content,
            "offset": offset,
            "total_chars": len(asset.content_markdown),
            "next_offset": next_offset if truncated else None,
            "truncated": truncated,
        }

    def _resolve_live_skill(
        self, *, request: ChatAskRequest, arguments: dict[str, Any], state: ToolOrchestrationState
    ) -> tuple[WorkspaceSkillItem | None, dict[str, Any] | None]:
        if self._workspace_skill_repository is None:
            state.add_warning("workspace_skill_repository_missing")
            return None, self._error(
                "workspace_skill_repository_missing", "Workspace skills are not configured."
            )
        skill_id = arguments.get("skill_id") if isinstance(arguments.get("skill_id"), str) else None
        skill_name = (
            arguments.get("skill_name") if isinstance(arguments.get("skill_name"), str) else None
        )
        if not (skill_id and skill_id.strip()) and not (skill_name and skill_name.strip()):
            return None, self._error(
                "workspace_skill_identifier_missing", "Provide skill_id or skill_name."
            )
        allowed_skill_ids = (
            request.agent_allowed_workspace_skill_ids or [] if request.agent_id else None
        )
        default_visible = None if request.agent_id else True
        skill = self._workspace_skill_repository.get_active_chat_instruction(
            workspace_id=request.workspace_id,
            skill_id=skill_id,
            skill_name=skill_name,
            skill_ids=allowed_skill_ids,
            visible_to_default_assistant=default_visible,
        )
        if skill is None:
            state.add_warning("workspace_skill_not_found")
            return None, self._error(
                "workspace_skill_not_found",
                "Workspace skill was not found, inactive, or not allowed for this agent.",
            )
        return skill, None

    def _still_live(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        skill_id: str,
        state: ToolOrchestrationState,
    ) -> bool:
        """Close the version-read/access-revoke window before returning content."""
        skill, error = self._resolve_live_skill(request=request, arguments=arguments, state=state)
        return error is None and skill is not None and skill.id == skill_id

    def _resolve_version(
        self, *, skill: WorkspaceSkillItem, digest: str | None
    ) -> tuple[WorkspaceSkillVersionSnapshot | None, dict[str, Any] | None]:
        if self._workspace_skill_repository is None or not digest:
            return None, self._error(
                "workspace_skill_version_not_found", "The selected skill version is unavailable."
            )
        version = self._workspace_skill_repository.get_version(
            skill_id=skill.id, workspace_id=skill.workspace_id, version_digest=digest
        )
        if version is None:
            return None, self._error(
                "workspace_skill_version_not_found", "The selected skill version is unavailable."
            )
        return version, None

    @staticmethod
    def _requested_digest(
        *,
        skill: WorkspaceSkillItem,
        arguments: dict[str, Any],
        cursor: dict[str, Any] | None,
        state: ToolOrchestrationState,
    ) -> str | None:
        binding = next(
            (
                item
                for item in state.workspace_skill_version_bindings
                if item.skill_id == skill.id
            ),
            None,
        )
        if binding is not None:
            return binding.version_digest
        requested = arguments.get("version_digest")
        if isinstance(requested, str) and requested.strip():
            return requested.strip()
        if cursor is not None:
            return cursor["version_digest"]
        return skill.version_digest

    def _resolve_asset(
        self, *, version: WorkspaceSkillVersionSnapshot, raw_path: object
    ) -> tuple[WorkspaceSkillAsset | None, dict[str, Any] | None]:
        if raw_path is None:
            asset = next(
                (item for item in version.content_bundle.assets if item.kind == "main"), None
            )
            asset = asset or (
                version.content_bundle.assets[0] if version.content_bundle.assets else None
            )
            return (
                (asset, None)
                if asset is not None
                else (
                    None,
                    self._error("workspace_skill_path_not_found", "Skill has no readable files."),
                )
            )
        if not isinstance(raw_path, str):
            return None, self._error("workspace_skill_path_invalid", "Path must be a string.")
        path, path_error = self._normalized_path(raw_path)
        if path_error is not None:
            return None, path_error
        asset = next((item for item in version.content_bundle.assets if item.path == path), None)
        if asset is None:
            return None, self._error(
                "workspace_skill_path_not_found", "Path is not in the selected skill bundle."
            )
        return asset, None

    def _normalized_path(self, raw_path: str) -> tuple[str | None, dict[str, Any] | None]:
        candidate = raw_path.strip()
        if not candidate or "\x00" in candidate:
            return None, self._error(
                "workspace_skill_path_invalid", "Path must name a bundle file."
            )
        path = PurePosixPath(candidate)
        if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
            return None, self._error(
                "workspace_skill_path_invalid", "Path must stay within the skill bundle."
            )
        return str(path), None

    @staticmethod
    def _encode_cursor(skill_id: str, version_digest: str, offset: int) -> str:
        payload = json.dumps(
            {"skill_id": skill_id, "version_digest": version_digest, "offset": offset},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii")

    def _decode_cursor(self, value: object) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if value is None:
            return None, None
        if not isinstance(value, str) or not value:
            return None, self._error("workspace_skill_cursor_invalid", "Cursor is invalid.")
        try:
            cursor = json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, self._error("workspace_skill_cursor_invalid", "Cursor is invalid.")
        if (
            not isinstance(cursor, dict)
            or not isinstance(cursor.get("skill_id"), str)
            or not isinstance(cursor.get("version_digest"), str)
            or not isinstance(cursor.get("offset"), int)
            or isinstance(cursor["offset"], bool)
            or cursor["offset"] < 0
        ):
            return None, self._error("workspace_skill_cursor_invalid", "Cursor is invalid.")
        return cursor, None

    @staticmethod
    def _record_binding(
        *,
        state: ToolOrchestrationState,
        skill: WorkspaceSkillItem,
        version: WorkspaceSkillVersionSnapshot,
        read_address: dict[str, object] | None = None,
    ) -> None:
        state.metadata_sources.add("workspace_skill")
        if not any(binding.skill_id == skill.id for binding in state.workspace_skill_version_bindings):
            state.workspace_skill_version_bindings.append(
                WorkspaceSkillVersionBinding(
                    skill_id=skill.id,
                    version_digest=version.version_digest,
                )
            )
        if not any(
            isinstance(item, dict) and item.get("id") == skill.id
            for item in state.workspace_skills_read
        ):
            state.workspace_skills_read.append({
                "id": skill.id,
                "name": skill.name,
                "title": skill.title,
                "summary": skill.summary,
                "content_sha256": skill.content_sha256,
                "version_digest": version.version_digest,
            })
        if read_address is not None:
            address = {
                "skill_id": skill.id,
                "version_digest": version.version_digest,
                **read_address,
            }
            if address not in state.workspace_skill_read_addresses:
                state.workspace_skill_read_addresses.append(address)

    @staticmethod
    def _error(error_code: str, user_message: str) -> dict[str, Any]:
        return build_tool_error(
            error_code=error_code,
            error_category=ErrorCategory.RECOVERABLE,
            user_message=user_message,
            retry_recommended=False,
            can_continue=True,
        )
