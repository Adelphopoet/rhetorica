"""Owner-scoped coding admission and inspection, shared by chat and scenarios."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.coding import (
    AgentPolicySnapshot,
    AgentTask,
    CodingPlanDraft,
    CodingRun,
    CodingRunAdmission,
    CodingRunAdmissionResult,
    CodingRunPhase,
    CodingRunStatus,
    CodingRunView,
    CodingTeamMemberCapability,
    CreateCodingRunRequest,
    DataWarehouseBinding,
    ExecutionPrincipal,
    GitLabWriteScope,
)
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillVersionBinding
from rhetorica_backend.domain.chat.gitlab import GitLabService
from rhetorica_backend.domain.coding.errors import CodingRunError
from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID
from rhetorica_backend.domain.coding.progress import CodingRunProgressReader
from rhetorica_backend.domain.coding.teams import legacy_projection
from rhetorica_backend.infra.coding.artifact_relay import SignedS3CodingArtifactRelay
from rhetorica_backend.infra.coding.journal import CodingOperationJournal
from rhetorica_backend.infra.postgres.agent_policy_repository import AgentPolicyRepository
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.coding_environment_repository import (
    CodingEnvironmentRepository,
)
from rhetorica_backend.infra.postgres.coding_publication_repository import CodingPublicationLedger
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.gitlab_connector_repository import GitLabConnectorRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository


class CodingRunService:
    def __init__(
        self,
        *,
        runs: CodingRunRepository,
        users: UserAccountRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
        policies: AgentPolicyRepository,
        gitlab_connectors: GitLabConnectorRepository,
        databases: ConnectorRepository,
        environments: CodingEnvironmentRepository,
        chats: ChatRepository,
        skills: WorkspaceSkillRepository,
        artifacts: SignedS3CodingArtifactRelay,
        publications: CodingPublicationLedger,
        enabled: bool,
        gitlab: GitLabService | None = None,
        journal: CodingOperationJournal | None = None,
        launch_defaults: dict[str, CreateCodingRunRequest] | None = None,
        workspace_defaults=None,
        teams=None,
    ):
        self.teams = teams
        self.runs = runs
        self.users = users
        self.workspaces = workspaces
        self.agents = agents
        self.policies = policies
        self.gitlab_connectors = gitlab_connectors
        self.databases = databases
        self.environments = environments
        self.chats = chats
        self.skills = skills
        self.artifacts = artifacts
        self.publications = publications
        self.enabled = enabled
        self.gitlab = gitlab or GitLabService()
        self.journal = journal
        self.progress = CodingRunProgressReader(runs)
        from rhetorica_backend.domain.coding.resources import CodingResourcePolicy

        self.resources = CodingResourcePolicy(
            users=users,
            workspaces=workspaces,
            agents=agents,
            policies=policies,
            gitlab_connectors=gitlab_connectors,
            databases=databases,
            environments=environments,
            enabled=enabled,
            launch_defaults=launch_defaults,
            workspace_defaults=workspace_defaults,
            teams=teams,
        )

    def require_actor(self, user, **kwargs):
        return self.resources.require_actor(user, **kwargs)

    def agent(self, user, agent_id):
        return self.resources.agent(user, agent_id)

    def root_policy(self, user, root_agent_id):
        return self.resources.root_policy(user, root_agent_id)

    def validate_resources(self, request, user):
        return self.resources.validate_resources(request, user)

    def capabilities(self, user, **kwargs):
        return self.resources.capabilities(user, **kwargs)

    def team_options(self, user, **kwargs):
        return self.resources.team_options(user, **kwargs)

    def create(
        self,
        request: CreateCodingRunRequest,
        user,
        *,
        auth_kind: str,
        idempotency_key: str,
        source: str = "web_chat",
        scenario_id: str | None = None,
        confirmed_plan_id: str | None = None,
        plan_draft: CodingPlanDraft | None = None,
    ) -> CodingRunAdmissionResult:
        user = self.require_actor(user, auth_kind=auth_kind, write=True)
        if source == "web_chat" and not confirmed_plan_id:
            raise CodingRunError("coding_confirmation_required", status_code=409)
        if not self.enabled:
            raise CodingRunError("coding_disabled", status_code=503)
        if not idempotency_key or len(idempotency_key) > 128:
            raise CodingRunError("idempotency_key_required")
        root, team, children, environment = self.resources.admit_resources(request, user)
        if source == "web_chat":
            conversation = self.chats.get_conversation(
                request.conversation_id or "", owner_user_id=user.id
            )
            if (
                conversation is None
                or conversation.workspace_id != user.workspace_id
                or conversation.agent_id != request.root_agent_id
            ):
                raise CodingRunError("coding_conversation_mismatch", status_code=404)
        fingerprint = hashlib.sha256(
            json.dumps(
                {**request.model_dump(mode="json"), "source": source, "scenario_id": scenario_id},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        run_id = str(
            uuid5(NAMESPACE_URL, f"coding/{user.workspace_id}/{user.id}/{idempotency_key}")
        )
        # Retry before resolving the moving branch: immutable base and environment remain admitted.
        from rhetorica_backend.domain.coding.errors import CodingRunNotFound

        try:
            existing = self.runs.get_admission(
                run_id=run_id, workspace_id=user.workspace_id, owner_user_id=user.id
            )
        except CodingRunNotFound:
            existing = None
        if existing is not None:
            if existing.run.input_digest != fingerprint:
                raise CodingRunError("coding_idempotency_conflict", status_code=409)
            return CodingRunAdmissionResult(
                run=existing.run, created=False, workflow_id=self.runs.workflow_id(run_id)
            )
        connection = self.gitlab_connectors.get_connection_by_id(
            workspace_id=user.workspace_id, connector_id=request.gitlab_connector_id
        )
        if connection is None:
            raise CodingRunError("resource_access_revoked", status_code=403)
        branch = self.gitlab.get_branch(
            connection, project_path=request.project_id, branch=request.target_branch
        )
        if not branch or not (branch.get("commit") or {}).get("id"):
            raise CodingRunError("coding_target_branch_unavailable")
        now = datetime.now(UTC)
        root_id = request.root_agent_id or DEFAULT_ASSISTANT_POLICY_ID
        profiles = {child.id: child for child in children}
        member_policies = {}
        for member in team.members:
            policy = self.policies.get_agent_policy(
                workspace_id=user.workspace_id, agent_id=member.agent_profile_id
            )
            if policy is None:
                raise CodingRunError("coding_specialist_policy_required", status_code=403)
            member_policies[member.id] = policy
        candidate_members = [
            m for m in team.members if m.capability == CodingTeamMemberCapability.CANDIDATE
        ]
        executing = (candidate_members or team.members)[0]
        developer_policy = member_policies[executing.id]
        # Intersection of candidate members' grants: a team can only narrow write access.
        path_sets = [
            {
                path
                for scope in member_policies[member.id].gitlab_write_scopes
                if scope.connector_id == request.gitlab_connector_id
                and scope.project_id == request.project_id
                and request.target_branch in scope.target_branches
                for path in scope.allowed_paths
            }
            for member in candidate_members or [executing]
        ]
        allowed_paths = sorted(set.intersection(*path_sets)) if path_sets else []
        # Empty publication scope permits preparation while keeping automatic write closed.
        scope = GitLabWriteScope(
            connector_id=request.gitlab_connector_id,
            project_id=request.project_id,
            target_branches=[request.target_branch],
            allowed_paths=allowed_paths or ["models"],
        )
        developer_agent_id, qa_agent_id = legacy_projection(team)
        run = CodingRun(
            id=run_id,
            workspace_id=user.workspace_id,
            owner_user_id=user.id,
            source=source,
            conversation_id=request.conversation_id,
            scenario_id=scenario_id,
            root_agent_id=root_id,
            developer_agent_id=developer_agent_id,
            qa_agent_id=qa_agent_id,
            team_snapshot=team,
            idempotency_key=idempotency_key,
            input_digest=fingerprint,
            phase=CodingRunPhase.PREPARING,
            status=CodingRunStatus.QUEUED,
            repository={
                "gitlab_connector_id": request.gitlab_connector_id,
                "project_id": request.project_id,
                "target_branch": request.target_branch,
                "base_sha": branch["commit"]["id"],
            },
            data_warehouse=DataWarehouseBinding(
                connector_id=request.database_connector_id,
                dbt_project_subdir=request.dbt_project_subdir,
            )
            if request.database_connector_id
            else None,
            environment_profile_id=environment.id,
            environment_profile_revision=environment.revision,
            policy_revision=root.revision,
            created_at=now,
            deadline=now
            + timedelta(seconds=min(root.max_runtime_seconds, environment.max_runtime_seconds)),
        )
        principal = ExecutionPrincipal(
            actor_user_id=user.id,
            workspace_id=user.workspace_id,
            entry_source=source,
            root_agent_id=root_id,
            executing_agent_id=executing.agent_profile_id,
            parent_agent_id=root_id,
            run_id=run_id,
        )
        admission = CodingRunAdmission(
            run=run,
            principal=principal,
            policy_snapshot=AgentPolicySnapshot(
                workspace_id=user.workspace_id,
                root_agent_id=root_id,
                executing_agent_id=executing.agent_profile_id,
                root_settings=root,
                executing_settings=developer_policy,
                resource_connector_id=request.gitlab_connector_id,
                run_scope=scope,
            ),
            environment_profile=environment,
        )
        # Put task instructions outside Temporal; deterministic task IDs make crash recovery idempotent.
        from rhetorica_backend.domain.coding.instructions import encode_instructions

        instruction = encode_instructions(request.task, plan_draft)
        ref = self.artifacts.persist(
            payload=instruction,
            digest=hashlib.sha256(instruction).hexdigest(),
            workspace_id=user.workspace_id,
            owner_user_id=user.id,
            run_id=run_id,
            task_id="instructions",
            kind="instructions",
        )
        coordinator_task_id = str(uuid5(NAMESPACE_URL, f"coding/{run_id}/coordinator"))
        tasks = [
            AgentTask(
                id=coordinator_task_id,
                workspace_id=user.workspace_id,
                owner_user_id=user.id,
                run_id=run_id,
                agent_profile_id=root_id,
                role="coordinator",
                team_member_label="Coordinator",
                status=CodingRunStatus.QUEUED,
                max_iterations=root.max_iterations,
                max_runtime_seconds=root.max_runtime_seconds,
                instruction_artifact_id=ref,
            )
        ]
        for member in team.members:
            profile = profiles[member.agent_profile_id]
            policy = member_policies[member.id]
            bindings = member.skill_version_bindings or [
                WorkspaceSkillVersionBinding(skill_id=s.id, version_digest=s.version_digest)
                for s in self.skills.list_active_chat_instructions(
                    workspace_id=user.workspace_id,
                    skill_ids=profile.allowed_workspace_skill_ids,
                    limit=100,
                )
                if s.version_digest
            ]
            tasks.append(
                AgentTask(
                    id=str(uuid5(NAMESPACE_URL, f"coding/{run_id}/member/{member.id}")),
                    workspace_id=user.workspace_id,
                    owner_user_id=user.id,
                    run_id=run_id,
                    parent_task_id=coordinator_task_id,
                    agent_profile_id=member.agent_profile_id,
                    role=member.storage_role,
                    team_member_id=member.id,
                    team_member_label=member.label,
                    status=CodingRunStatus.QUEUED,
                    max_iterations=min(root.max_iterations, policy.max_iterations),
                    max_runtime_seconds=min(root.max_runtime_seconds, policy.max_runtime_seconds),
                    instruction_artifact_id=ref,
                    skill_version_bindings=bindings,
                    skill_version_digests=[b.version_digest for b in bindings],
                )
            )
        return self.runs.admit(admission, tasks=tasks)

    def view(self, run_id, user, *, auth_kind="session", after=0):
        self.require_actor(user, auth_kind=auth_kind)
        scope = {"run_id": run_id, "workspace_id": user.workspace_id, "owner_user_id": user.id}
        run = self.runs.get(**scope)
        report = (
            self.runs.validation_report(report_id=run.validation_report_id, **scope)
            if run.validation_report_id
            else None
        )
        receipt = self.publications.get(**scope)
        return CodingRunView(
            run=run,
            progress=self.progress.read(**scope, after=after),
            report=report,
            receipt=receipt,
        )

    def diff(self, run_id, user, *, auth_kind="session", path=None, offset=0, max_chars=128_000):
        import difflib

        from rhetorica_backend.domain.coding.publication_diff import tree_from_candidate_archive

        view = self.view(run_id, user, auth_kind=auth_kind)
        tasks = view.progress.tasks
        coordinator = next((t for t in tasks if t.role == "coordinator"), None)
        if view.run.candidate is None or coordinator is None or not coordinator.checkpoint_ref:
            return {"diff": "", "truncated": False}
        # The signed artifact scope names whichever member produced the candidate.
        producer_task_id = str(self.artifacts.scope(view.run.candidate.artifact_id)["task_id"])
        scope = {"workspace_id": user.workspace_id, "owner_user_id": user.id, "run_id": run_id}
        base = tree_from_candidate_archive(
            self.artifacts.download_owned(
                artifact_id=coordinator.checkpoint_ref, task_id=coordinator.id, **scope
            )[0]
        )
        candidate = tree_from_candidate_archive(
            self.artifacts.download_owned(
                artifact_id=view.run.candidate.artifact_id, task_id=producer_task_id, **scope
            )[0]
        )
        if path is not None and path not in view.run.candidate.changed_paths:
            raise CodingRunError("coding_candidate_diff_path_not_changed")
        parts = []
        paths = [path] if path is not None else view.run.candidate.changed_paths
        for path in paths:
            before = base.get(path, b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            after = (
                candidate.get(path, b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            )
            part = "".join(
                difflib.unified_diff(before, after, fromfile=f"a/{path}", tofile=f"b/{path}")
            )
            parts.append(part)
        raw = "".join(parts)
        content = raw[offset : offset + max_chars]
        end = offset + len(content)
        return {
            "diff": content,
            "truncated": end < len(raw),
            "offset": offset,
            "next_offset": end if end < len(raw) else None,
            "total_chars": len(raw),
            "candidate_digest": view.run.candidate.digest,
            "base_sha": view.run.candidate.base_sha,
            "changed_paths": view.run.candidate.changed_paths,
        }

    def cancel(self, run_id, user, *, auth_kind="session"):
        self.require_actor(user, auth_kind=auth_kind, write=True)
        return self.runs.cancel(
            run_id=run_id, workspace_id=user.workspace_id, owner_user_id=user.id
        )
