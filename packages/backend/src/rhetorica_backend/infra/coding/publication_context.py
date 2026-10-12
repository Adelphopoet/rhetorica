"""Live publication context and owner-bound artifact adapter."""

import io

from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID
from rhetorica_backend.domain.coding.publication import PublicationWorkItem


class CodingPublicationContext:
    def __init__(self, service):
        self._service = service

    def load(self, *, run_id, principal):
        s = self._service
        admission = s.runs.get_admission(
            run_id=run_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        user = s.users.get_by_id(user_id=principal.actor_user_id)
        member = s.workspaces.membership(
            workspace_id=principal.workspace_id, user_id=principal.actor_user_id
        )
        developer = s.agents.get(
            agent_id=principal.executing_agent_id,
            workspace_id=principal.workspace_id,
            current_user_id=principal.actor_user_id,
            active_only=True,
        )
        root_id = admission.run.root_agent_id
        root = (
            s.policies.get_default_assistant_policy(workspace_id=principal.workspace_id)
            if root_id == DEFAULT_ASSISTANT_POLICY_ID
            else s.policies.get_agent_policy(workspace_id=principal.workspace_id, agent_id=root_id)
        )
        current = s.policies.get_agent_policy(
            workspace_id=principal.workspace_id, agent_id=principal.executing_agent_id
        )
        from rhetorica_backend.contracts.coding import GovernedAgentSettings

        connector = next(
            (
                c
                for c in s.gitlab_connectors.list(principal.workspace_id, 100)
                if c.id == admission.run.repository.gitlab_connector_id and c.is_active
            ),
            None,
        )
        root_profile = (
            None
            if root_id == DEFAULT_ASSISTANT_POLICY_ID
            else s.agents.get(
                agent_id=root_id,
                workspace_id=principal.workspace_id,
                current_user_id=principal.actor_user_id,
                active_only=True,
            )
        )
        root_active = root_id == DEFAULT_ASSISTANT_POLICY_ID or root_profile is not None
        return PublicationWorkItem(
            run=admission.run,
            admission=admission.policy_snapshot,
            current_executing=current or GovernedAgentSettings(),
            current_root=root,
            actor_authorized=bool(user and user.is_active and member and member.role != "viewer"),
            resource_accessible=bool(
                connector
                and developer
                and connector.name in developer.allowed_gitlab_connector_names
                and (
                    root_id == DEFAULT_ASSISTANT_POLICY_ID
                    or (
                        root_profile
                        and connector.name in root_profile.allowed_gitlab_connector_names
                    )
                )
            ),
            executing_agent_active=developer is not None,
            root_agent_active=root_active,
            validation_report=s.runs.validation_report(
                report_id=admission.run.validation_report_id,
                run_id=run_id,
                workspace_id=principal.workspace_id,
                owner_user_id=principal.actor_user_id,
            )
            if admission.run.validation_report_id
            else None,
            cancelled=admission.run.status in {"cancel_requested", "cancelled"},
        )


class CodingPublicationArtifacts:
    def __init__(self, relay):
        self._relay = relay

    def verify_owned(self, *, artifact_id, workspace_id, owner_user_id):
        record = self._relay.scope(artifact_id)
        return record["workspace_id"] == workspace_id and record["owner_user_id"] == owner_user_id

    def open_owned(self, *, artifact_id, workspace_id, owner_user_id):
        record = self._relay.scope(artifact_id)
        payload, _ = self._relay.download_owned(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            run_id=record["run_id"],
            task_id=record["task_id"],
        )
        return io.BytesIO(payload)
