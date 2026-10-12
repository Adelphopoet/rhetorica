"""Shared coding composition root; API and worker only select a transport."""

from rhetorica_backend.domain.coding.service import CodingRunService
from rhetorica_backend.domain.coding.settings import CodingSettings
from rhetorica_backend.infra.coding.artifact_relay import SignedS3CodingArtifactRelay
from rhetorica_backend.infra.object_storage import S3ObjectStorage, S3StorageConfig
from rhetorica_backend.infra.postgres.agent_policy_repository import AgentPolicyRepository
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.coding_defaults_repository import CodingDefaultsRepository
from rhetorica_backend.infra.postgres.coding_environment_repository import (
    CodingEnvironmentRepository,
)
from rhetorica_backend.infra.postgres.coding_publication_repository import CodingPublicationLedger
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.gitlab_connector_repository import GitLabConnectorRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import WorkspaceSkillRepository


def build_coding_service(settings: CodingSettings) -> CodingRunService:
    import logging

    for logger_name in ["botocore", "boto3", "httpcore"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    db = DatabaseRuntime(settings.database_url)
    storage = S3ObjectStorage(
        S3StorageConfig(
            settings.s3_endpoint,
            settings.s3_access_key,
            settings.s3_secret_key,
            settings.s3_bucket,
            settings.s3_region,
            settings.s3_secure,
        )
    )
    artifacts = SignedS3CodingArtifactRelay(
        storage=storage,
        signing_secret=settings.coding_artifact_signing_secret
        or settings.execution_runtime_internal_token,
    )
    from rhetorica_backend.domain.coding.teams import CodingAgentTeamValidator, CodingTeamAdmission
    from rhetorica_backend.infra.coding.journal import CodingOperationJournal
    from rhetorica_backend.infra.postgres.coding_team_repository import CodingAgentTeamRepository

    agents = AgentProfileRepository(db)
    policies = AgentPolicyRepository(db)
    gitlab_connectors = GitLabConnectorRepository(db)
    skills = WorkspaceSkillRepository(db)
    return CodingRunService(
        journal=CodingOperationJournal(storage),
        runs=CodingRunRepository(db),
        users=UserAccountRepository(db),
        workspaces=WorkspaceRepository(db),
        agents=agents,
        policies=policies,
        gitlab_connectors=gitlab_connectors,
        databases=ConnectorRepository(db),
        environments=CodingEnvironmentRepository(db),
        chats=ChatRepository(db),
        skills=skills,
        artifacts=artifacts,
        publications=CodingPublicationLedger(db),
        enabled=settings.coding_enabled,
        workspace_defaults=CodingDefaultsRepository(db),
        teams=CodingTeamAdmission(
            repository=CodingAgentTeamRepository(db),
            validator=CodingAgentTeamValidator(
                agents=agents,
                policies=policies,
                skills=skills,
                gitlab_connectors=gitlab_connectors,
            ),
        ),
    )


def build_coding_planning(settings: CodingSettings):
    from rhetorica_backend.domain.coding.chat_planning import CodingChatPlanningService
    from rhetorica_backend.domain.coding.planner import CodingCoordinatorPlanner
    from rhetorica_backend.infra.postgres.coding_plan_repository import CodingPlanRepository
    from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository

    service = build_coding_service(settings)
    db = DatabaseRuntime(settings.database_url)
    return CodingChatPlanningService(
        service=service,
        plans=CodingPlanRepository(db),
        planner=CodingCoordinatorPlanner(service=service, llms=LlmConnectorRepository(db)),
    )


def build_coding_defaults(settings: CodingSettings):
    from rhetorica_backend.domain.coding.workspace_defaults import CodingWorkspaceDefaultsService

    return CodingWorkspaceDefaultsService(
        service=build_coding_service(settings),
        repository=CodingDefaultsRepository(DatabaseRuntime(settings.database_url)),
    )


def build_coding_teams(settings: CodingSettings):
    from rhetorica_backend.domain.coding.team_service import CodingAgentTeamService
    from rhetorica_backend.domain.coding.teams import CodingAgentTeamValidator
    from rhetorica_backend.infra.postgres.coding_team_repository import CodingAgentTeamRepository

    db = DatabaseRuntime(settings.database_url)
    service = build_coding_service(settings)
    return CodingAgentTeamService(
        coding_service=service,
        repository=CodingAgentTeamRepository(db),
        validator=CodingAgentTeamValidator(
            agents=service.agents,
            policies=service.policies,
            skills=service.skills,
            gitlab_connectors=service.gitlab_connectors,
        ),
    )


def build_coding_lifecycle(settings: CodingSettings):
    from rhetorica_backend.domain.coding.executor import GovernedCodingExecutor
    from rhetorica_backend.domain.coding.lifecycle import CodingLifecycleService
    from rhetorica_backend.infra.coding.gitlab_publisher import GitLabPublicationService
    from rhetorica_backend.infra.coding.local_runner import LocalCodingRunner
    from rhetorica_backend.infra.coding.publication_context import (
        CodingPublicationArtifacts,
        CodingPublicationContext,
    )

    if settings.coding_runtime_mode != "local_docker":
        raise RuntimeError("coding_runtime_composition_unavailable")
    service = build_coding_service(settings)
    runner = LocalCodingRunner(
        base_url=settings.execution_runtime_url, token=settings.execution_runtime_internal_token
    )
    publisher = GitLabPublicationService(
        work_items=CodingPublicationContext(service),
        artifacts=CodingPublicationArtifacts(service.artifacts),
        ledger=service.publications,
        connectors=service.gitlab_connectors,
    )
    from rhetorica_backend.domain.coding.specialist_runtime import CodingSpecialistRuntime
    from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository

    db = DatabaseRuntime(settings.database_url)
    agents = CodingSpecialistRuntime(
        service=service,
        runner=runner,
        settings=settings,
        db=db,
        llms=LlmConnectorRepository(db),
    )
    from rhetorica_backend.domain.coding.team_runtime import CodingTeamCoordinatorRuntime

    lifecycle = CodingLifecycleService(
        runs=service.runs,
        executor=GovernedCodingExecutor(
            service=service,
            runner=runner,
            publisher=publisher,
            settings=settings,
            agents=agents,
            coordinator=CodingTeamCoordinatorRuntime(service=service, specialists=agents, db=db),
        ),
    )
    return lifecycle, service.runs
