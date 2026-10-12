from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from rhetorica_backend.contracts.admin_integrations import (
    GitLabConnectionConfig,
    LlmConnectionConfig,
    UpsertGitLabConnectorRequest,
    UpsertLlmConnectorRequest,
)
from rhetorica_backend.contracts.connectors import UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.workspaces import (
    CreateWorkspaceRequest,
    CreateWorkspaceResourceGrantRequest,
    UpsertWorkspaceMembershipRequest,
)
from rhetorica_backend.infra.postgres.airflow_connector_repository import (
    AirflowConnectorRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.documentation_connector_repository import (
    DocumentationConnectorRepository,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)
from rhetorica_backend.infra.postgres.kubernetes_connector_repository import (
    KubernetesConnectorRepository,
)
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)
from rhetorica_backend.infra.postgres.mcp_connector_repository import McpConnectorRepository
from rhetorica_backend.infra.postgres.models import (
    AirflowConnectorConfigOrm,
    DocumentationConnectorConfigOrm,
    KubernetesConnectorConfigOrm,
    MattermostConnectorConfigOrm,
    McpConnectorConfigOrm,
    McpToolCatalogOrm,
    RagConnectorConfigOrm,
    VictoriaMetricsConnectorConfigOrm,
    WorkspaceSkillOrm,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.victoriametrics_connector_repository import (
    VictoriaMetricsConnectorRepository,
)
from rhetorica_backend.infra.postgres.workspace_models import WorkspaceMembershipOrm, WorkspaceOrm
from rhetorica_backend.infra.postgres.workspace_repository import (
    WorkspaceConflictError,
    WorkspaceRepository,
)
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository
from rhetorica_backend.infra.secrets import InMemorySecretStore


class TrackingSecretStore(InMemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []
        self.reads: list[str] = []

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        self.reads.append(workspace_id)
        return super().get_secret(workspace_id, secret_key)

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        self.writes.append(workspace_id)
        super().set_secret(workspace_id, secret_key, value)


def _runtime(tmp_path: Path) -> DatabaseRuntime:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path / 'workspaces.db'}")
    runtime.create_schema()
    return runtime


def _bootstrap(runtime: DatabaseRuntime):
    users = UserAccountRepository(runtime)
    admin, _ = users.ensure_local_admin(
        workspace_id="platform",
        login="admin",
        password_hash="hashed",
    )
    analyst = users.create_local_user(
        workspace_id="platform",
        login="analyst",
        password_hash="hashed",
        role="analyst",
    )
    workspaces = WorkspaceRepository(runtime)
    workspaces.ensure_home_membership(user_id=admin.id)
    workspaces.ensure_home_membership(user_id=analyst.id)
    workspaces.create(
        CreateWorkspaceRequest(id="analytics", name="Analytics"),
        actor_user_id=admin.id,
    )
    workspaces.create(
        CreateWorkspaceRequest(id="science", name="Science"),
        actor_user_id=admin.id,
    )
    return admin, analyst, workspaces


def test_membership_roles_are_live_and_last_owner_is_preserved(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, analyst, repository = _bootstrap(runtime)

    saved = repository.upsert_membership(
        workspace_id="analytics",
        request=UpsertWorkspaceMembershipRequest(user_id=analyst.id, role="viewer"),
        actor_user_id=admin.id,
    )
    assert saved.role == "viewer"
    assert repository.membership(workspace_id="analytics", user_id=analyst.id) is not None
    assert repository.revoke_membership(
        workspace_id="analytics",
        user_id=analyst.id,
        actor_user_id=admin.id,
    )
    assert repository.membership(workspace_id="analytics", user_id=analyst.id) is None
    with pytest.raises(WorkspaceConflictError, match="last_workspace_owner_required"):
        repository.revoke_membership(
            workspace_id="analytics",
            user_id=admin.id,
            actor_user_id=admin.id,
        )
    assert [event.action for event in repository.list_audit(workspace_id="analytics")][:2] == [
        "revoked",
        "upserted",
    ]


def test_default_workspace_membership_is_guaranteed_and_identifies_user(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, analyst, repository = _bootstrap(runtime)

    default_membership = repository.membership(workspace_id="default", user_id=analyst.id)
    assert default_membership is not None
    assert default_membership.role == "analyst"

    listed = repository.list_memberships(workspace_id="default")
    analyst_item = next(item for item in listed if item.user_id == analyst.id)
    assert analyst_item.user_login == "analyst"

    with pytest.raises(WorkspaceConflictError, match="default_workspace_membership_required"):
        repository.revoke_membership(
            workspace_id="default",
            user_id=analyst.id,
            actor_user_id=admin.id,
        )
    with pytest.raises(WorkspaceConflictError, match="default_workspace_membership_required"):
        repository.upsert_membership(
            workspace_id="default",
            request=UpsertWorkspaceMembershipRequest(user_id=analyst.id, role="viewer"),
            actor_user_id=admin.id,
        )


def test_shared_connector_uses_owner_secret_without_copy_or_transitive_grant(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    admin, _analyst, workspaces = _bootstrap(runtime)
    secrets = TrackingSecretStore()
    connectors = ConnectorRepository(runtime, secret_store=secrets)
    source = connectors.upsert(
        UpsertConnectorRequest(
            name="lake",
            workspace_id="platform",
            connection=EngineConnectionConfig(
                engine="trino",
                host="trino.internal",
                port=8443,
                username="rhetorica",
                password="owner-secret",
                catalog="iceberg",
                schema="analytics",
                secure=True,
            ),
        )
    )
    grant = workspaces.create_resource_grant(
        CreateWorkspaceResourceGrantRequest(
            resource_kind="database_connector",
            resource_id=source.id,
            owner_workspace_id="platform",
            recipient_workspace_id="analytics",
            alias="shared-lake",
        ),
        actor_user_id=admin.id,
    )

    resolved = connectors.get_connection("analytics", "shared-lake")
    assert resolved is not None
    assert resolved.password == "owner-secret"
    assert secrets.writes == ["platform"]
    assert secrets.reads[-1] == "platform"
    shared_item = connectors.get("analytics", "shared-lake")
    assert shared_item is not None
    assert shared_item.id == source.id

    with pytest.raises(LookupError, match="workspace_resource_not_found"):
        workspaces.create_resource_grant(
            CreateWorkspaceResourceGrantRequest(
                resource_kind="database_connector",
                resource_id=source.id,
                owner_workspace_id="analytics",
                recipient_workspace_id="science",
                alias="transitive-lake",
            ),
            actor_user_id=admin.id,
        )

    assert workspaces.revoke_resource_grant(
        grant_id=grant.id,
        workspace_id="analytics",
        actor_user_id=admin.id,
    )
    assert connectors.get_connection("analytics", "shared-lake") is None


def test_local_name_blocks_shared_alias_in_same_workspace(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, _analyst, workspaces = _bootstrap(runtime)
    connectors = ConnectorRepository(runtime, secret_store=InMemorySecretStore())
    source = connectors.upsert(
        UpsertConnectorRequest(
            name="source",
            workspace_id="platform",
            connection=EngineConnectionConfig(
                engine="trino", host="source", port=8080, username="user"
            ),
        )
    )
    connectors.upsert(
        UpsertConnectorRequest(
            name="lake",
            workspace_id="analytics",
            connection=EngineConnectionConfig(
                engine="trino", host="local", port=8080, username="user"
            ),
        )
    )

    with pytest.raises(WorkspaceConflictError, match="workspace_resource_alias_conflict"):
        workspaces.create_resource_grant(
            CreateWorkspaceResourceGrantRequest(
                resource_kind="database_connector",
                resource_id=source.id,
                owner_workspace_id="platform",
                recipient_workspace_id="analytics",
                alias="lake",
            ),
            actor_user_id=admin.id,
        )


def test_shared_gitlab_connector_uses_owner_token_without_copy(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, _analyst, workspaces = _bootstrap(runtime)
    secrets = TrackingSecretStore()
    connectors = GitLabConnectorRepository(runtime, secret_store=secrets)
    source = connectors.upsert(
        UpsertGitLabConnectorRequest(
            name="source-gitlab",
            workspace_id="platform",
            connection=GitLabConnectionConfig(
                provider="gitlab",
                base_url="https://gitlab.example.test",
                api_token="owner-token",
                use_when="Publish governed coding changes.",
            ),
        )
    )
    workspaces.create_resource_grant(
        CreateWorkspaceResourceGrantRequest(
            resource_kind="gitlab_connector",
            resource_id=source.id,
            owner_workspace_id="platform",
            recipient_workspace_id="analytics",
            alias="shared-gitlab",
        ),
        actor_user_id=admin.id,
    )

    shared = connectors.get_connection("analytics", "shared-gitlab")

    assert shared is not None
    assert shared.api_token == "owner-token"
    assert secrets.writes == ["platform"]
    assert secrets.reads[-1] == "platform"


def test_shared_llm_connector_uses_owner_secret_and_recipient_alias(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, _analyst, workspaces = _bootstrap(runtime)
    secrets = TrackingSecretStore()
    connectors = LlmConnectorRepository(runtime, secret_store=secrets)
    source = connectors.upsert(
        UpsertLlmConnectorRequest(
            name="deepseek-source",
            workspace_id="platform",
            is_default=True,
            connection=LlmConnectionConfig(
                base_url="https://llm.example.test",
                model="deepseek-v4",
                api_key="owner-key",
            ),
        )
    )
    grant = workspaces.create_resource_grant(
        CreateWorkspaceResourceGrantRequest(
            resource_kind="llm_connector",
            resource_id=source.id,
            owner_workspace_id="platform",
            recipient_workspace_id="analytics",
            alias="deepseek",
        ),
        actor_user_id=admin.id,
    )

    listed = connectors.list("analytics", limit=100)
    shared_item = next(item for item in listed if item.name == "deepseek")
    assert shared_item.workspace_id == "analytics"
    assert shared_item.is_default is False
    assert shared_item.has_api_key is True
    shared = connectors.get_connection("analytics", "deepseek")
    assert shared is not None
    assert shared.api_key == "owner-key"
    assert secrets.writes == ["platform"]
    assert secrets.reads[-1] == "platform"

    assert workspaces.revoke_resource_grant(
        grant_id=grant.id,
        workspace_id="analytics",
        actor_user_id=admin.id,
    )
    assert connectors.get_connection("analytics", "deepseek") is None


def test_all_declared_resource_kinds_resolve_shared_resources(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, _analyst, workspaces = _bootstrap(runtime)
    now = datetime.now(tz=UTC)
    rows = {
        "rag_connector": RagConnectorConfigOrm(
            workspace_id="platform",
            name="rag-source",
            base_url="https://rag.example.test",
            api_key="rag-secret",
            created_at=now,
            updated_at=now,
        ),
        "documentation_connector": DocumentationConnectorConfigOrm(
            workspace_id="platform",
            name="docs-source",
            base_url="https://docs.example.test",
            qdrant_base_url="https://qdrant.example.test",
            api_token="docs-secret",
            created_at=now,
            updated_at=now,
        ),
        "mcp_connector": McpConnectorConfigOrm(
            workspace_id="platform",
            name="mcp-source",
            url="https://mcp.example.test",
            created_at=now,
            updated_at=now,
        ),
        "airflow_connector": AirflowConnectorConfigOrm(
            workspace_id="platform",
            name="airflow-source",
            base_url="https://airflow.example.test",
            username="reader",
            password="airflow-secret",
            use_when="Inspect DAGs.",
            created_at=now,
            updated_at=now,
        ),
        "kubernetes_connector": KubernetesConnectorConfigOrm(
            workspace_id="platform",
            name="kubernetes-source",
            base_url="https://kubernetes.example.test",
            bearer_token="kubernetes-secret",
            use_when="Inspect workloads.",
            namespace_allowlist=["default"],
            created_at=now,
            updated_at=now,
        ),
        "victoriametrics_connector": VictoriaMetricsConnectorConfigOrm(
            workspace_id="platform",
            name="metrics-source",
            base_url="https://metrics.example.test",
            bearer_token="metrics-secret",
            use_when="Inspect metrics.",
            created_at=now,
            updated_at=now,
        ),
        "mattermost_connector": MattermostConnectorConfigOrm(
            workspace_id="platform",
            name="mattermost-source",
            base_url="https://mattermost.example.test",
            bot_token="mattermost-secret",
            created_at=now,
            updated_at=now,
        ),
        "workspace_skill": WorkspaceSkillOrm(
            workspace_id="platform",
            name="skill-source",
            capability_family="chat_instruction",
            engine_scope="any",
            priority=100,
            source_kind="manual_markdown",
            status="active",
            validation_status="valid",
            title="Shared skill",
            content_markdown="Use governed SQL.",
            created_at=now,
            updated_at=now,
        ),
    }
    with runtime.session() as session:
        session.add_all(rows.values())
        session.flush()
        mcp_tool = McpToolCatalogOrm(
            connector_id=rows["mcp_connector"].id,
            workspace_id="platform",
            connector_name="mcp-source",
            remote_tool_name="lookup",
            exposed_tool_name="mcp_source__lookup",
            input_schema={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(mcp_tool)
        session.commit()

    for kind, row in rows.items():
        workspaces.create_resource_grant(
            CreateWorkspaceResourceGrantRequest(
                resource_kind=kind,
                resource_id=row.id,
                owner_workspace_id="platform",
                recipient_workspace_id="analytics",
                alias=f"shared-{kind}",
            ),
            actor_user_id=admin.id,
        )

    rag = RagConnectorRepository(runtime)
    assert rag.list("analytics", 10)[0].name == "shared-rag_connector"
    assert rag.get_connection("analytics", "shared-rag_connector").api_key == "rag-secret"

    docs = DocumentationConnectorRepository(runtime)
    shared_docs = docs.get("analytics", "shared-documentation_connector")
    assert shared_docs is not None
    assert shared_docs.workspace_id == "analytics"
    assert docs.resolve_secrets(shared_docs.connection, "analytics", shared_docs.id).api_token == (
        "docs-secret"
    )
    assert docs.execution_scope(
        workspace_id="analytics",
        name="shared-documentation_connector",
    ) == ("platform", "docs-source")

    airflow = AirflowConnectorRepository(runtime)
    assert airflow.get_connection("analytics", "shared-airflow_connector").password == (
        "airflow-secret"
    )
    kubernetes = KubernetesConnectorRepository(runtime)
    assert kubernetes.get_connection(
        "analytics", "shared-kubernetes_connector"
    ).bearer_token == "kubernetes-secret"
    metrics = VictoriaMetricsConnectorRepository(runtime)
    assert metrics.get_connection(
        "analytics", "shared-victoriametrics_connector"
    ).bearer_token == "metrics-secret"
    mattermost = MattermostConnectorRepository(runtime)
    assert mattermost.get_connection("analytics", "shared-mattermost_connector").bot_token == (
        "mattermost-secret"
    )

    mcp = McpConnectorRepository(runtime)
    assert mcp.get_runtime_connector(
        workspace_id="analytics",
        name="shared-mcp_connector",
    ).url == "https://mcp.example.test"
    shared_tools = mcp.list_active_tools_for_connector(
        workspace_id="analytics",
        connector_name="shared-mcp_connector",
    )[0]
    assert shared_tools[0].connector_name == "shared-mcp_connector"
    assert shared_tools[0].workspace_id == "analytics"

    skills = WorkspaceSkillRepository(runtime)
    shared_skill = skills.get_active_chat_instruction(
        workspace_id="analytics",
        skill_name="shared-workspace_skill",
    )
    assert shared_skill is not None
    assert shared_skill.workspace_id == "analytics"


@pytest.mark.parametrize(
    "unavailable",
    ["membership_inactive", "membership_deleted", "workspace_inactive", "workspace_deleted"],
)
def test_session_preference_falls_back_without_changing_identity(
    tmp_path: Path, unavailable: str
) -> None:
    runtime = _runtime(tmp_path)
    admin, _, repository = _bootstrap(runtime)
    assert repository.remember_selected_workspace(user_id=admin.id, workspace_id="analytics")
    assert repository.session_membership(user_id=admin.id).workspace_id == "analytics"
    with runtime.session() as session:
        if unavailable.startswith("membership"):
            row = session.get(WorkspaceMembershipOrm, ("analytics", admin.id))
        else:
            row = session.get(WorkspaceOrm, "analytics")
        if unavailable.endswith("deleted"):
            session.delete(row)
        else:
            row.is_active = False
        session.commit()
    assert repository.session_membership(user_id=admin.id).workspace_id == "default"
    assert (
        repository.session_membership(user_id=admin.id, requested_workspace_id="analytics") is None
    )
    assert not repository.remember_selected_workspace(user_id=admin.id, workspace_id="analytics")
    assert UserAccountRepository(runtime).get_by_id(user_id=admin.id).workspace_id == "platform"


def test_latest_explicit_workspace_choice_replaces_preference(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    admin, analyst, repository = _bootstrap(runtime)
    assert repository.session_membership(user_id=admin.id).workspace_id == "platform"
    assert repository.remember_selected_workspace(user_id=admin.id, workspace_id="analytics")
    assert repository.remember_selected_workspace(user_id=admin.id, workspace_id="science")
    assert repository.session_membership(user_id=admin.id).workspace_id == "science"
    assert not repository.remember_selected_workspace(user_id=analyst.id, workspace_id="science")
    assert repository.session_membership(user_id=analyst.id).workspace_id == "platform"
    assert repository.remember_selected_workspace(user_id=admin.id, workspace_id="default")
    assert repository.session_membership(user_id=admin.id).workspace_id == "default"
