"""Durable task reader for immutable, still-authorized workspace skills."""

from __future__ import annotations

from rhetorica_backend.contracts.coding import (
    AgentTask,
    ExecutionPrincipal,
    SkillReadAddress,
)
from rhetorica_backend.contracts.workspace_skills import (
    WorkspaceSkillManifestEntry,
    WorkspaceSkillReadFragment,
)
from rhetorica_backend.domain.coding.errors import CodingTaskConflict, CodingToolAccessDenied
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository


class CodingWorkspaceSkillReader:
    """Read a bound version, while checking live actor, agent and skill access."""

    def __init__(
        self,
        *,
        runs: CodingRunRepository,
        users: UserAccountRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
        skills: WorkspaceSkillRepository,
    ) -> None:
        self._runs = runs
        self._users = users
        self._workspaces = workspaces
        self._agents = agents
        self._skills = skills

    def read(
        self,
        *,
        principal: ExecutionPrincipal,
        skill_id: str,
        version_digest: str,
        path: str,
        offset: int = 0,
        max_chars: int = 24_000,
    ) -> WorkspaceSkillReadFragment:
        if principal.task_id is None or principal.executing_agent_id is None:
            raise CodingToolAccessDenied("coding_skill_reader_task_required")
        address = SkillReadAddress(
            skill_id=skill_id,
            version_digest=version_digest,
            path=path,
            offset=offset,
            max_chars=max_chars,
        )
        task = self._authorize(principal)
        if (address.skill_id, address.version_digest) not in {
            (binding.skill_id, binding.version_digest) for binding in task.skill_version_bindings
        }:
            raise CodingToolAccessDenied("coding_skill_reader_version_unbound")
        profile = self._agents.get(
            agent_id=task.agent_profile_id,
            workspace_id=task.workspace_id,
            current_user_id=task.owner_user_id,
            active_only=True,
        )
        if profile is None:
            raise CodingToolAccessDenied("coding_skill_reader_agent_unavailable")
        skill = self._skills.get_active_chat_instruction(
            workspace_id=task.workspace_id,
            skill_id=address.skill_id,
            skill_ids=profile.allowed_workspace_skill_ids,
        )
        if skill is None:
            raise CodingToolAccessDenied("coding_skill_reader_access_revoked")
        version = self._skills.get_version(
            skill_id=skill.id,
            workspace_id=task.workspace_id,
            version_digest=address.version_digest,
        )
        if version is None:
            raise CodingToolAccessDenied("coding_skill_reader_version_not_found")
        asset = next(
            (item for item in version.content_bundle.assets if item.path == address.path), None
        )
        if asset is None:
            raise CodingToolAccessDenied("coding_skill_reader_path_not_found")
        if address.offset > len(asset.content_markdown):
            raise CodingToolAccessDenied("coding_skill_reader_offset_invalid")
        content = asset.content_markdown[address.offset : address.offset + address.max_chars]
        self._record_read_address(task=task, address=address)
        next_offset = address.offset + len(content)
        available_files = (
            [
                WorkspaceSkillManifestEntry.from_asset(bundle_asset)
                for bundle_asset in version.content_bundle.assets
            ]
            if address.offset == 0 and asset.kind == "main"
            else None
        )
        return WorkspaceSkillReadFragment(
            skill_id=skill.id,
            version_digest=version.version_digest,
            path=asset.path,
            content=content,
            offset=address.offset,
            total_chars=len(asset.content_markdown),
            next_offset=next_offset if next_offset < len(asset.content_markdown) else None,
            truncated=next_offset < len(asset.content_markdown),
            available_files=available_files,
        )

    def _authorize(self, principal: ExecutionPrincipal) -> AgentTask:
        if principal.auth_kind != "session":
            raise CodingToolAccessDenied("coding_skill_reader_web_context_required")
        user = self._users.get_by_id(user_id=principal.actor_user_id)
        if user is None or not user.is_active:
            raise CodingToolAccessDenied("coding_skill_reader_actor_unavailable")
        if (
            self._workspaces.membership(
                workspace_id=principal.workspace_id,
                user_id=principal.actor_user_id,
            )
            is None
        ):
            raise CodingToolAccessDenied("coding_skill_reader_workspace_access_denied")
        task = self._runs.get_task(
            task_id=principal.task_id or "",
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        if task.run_id != principal.run_id or task.agent_profile_id != principal.executing_agent_id:
            raise CodingToolAccessDenied("coding_skill_reader_principal_mismatch")
        return task

    def _record_read_address(self, *, task: AgentTask, address: SkillReadAddress) -> None:
        if address in task.skill_read_addresses:
            return
        try:
            self._runs.checkpoint_task(
                task.model_copy(
                    update={"skill_read_addresses": [*task.skill_read_addresses, address]}
                ),
                expected_version=task.version,
                event_type="skill.fragment_read",
            )
        except CodingTaskConflict:
            current = self._runs.get_task(
                task_id=task.id,
                workspace_id=task.workspace_id,
                owner_user_id=task.owner_user_id,
            )
            if address not in current.skill_read_addresses:
                raise
