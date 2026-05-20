from functools import lru_cache

from rhetorica_backend.domain.chat.orchestration.factory import ChatServiceFactory
from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.llm.capability_registry import (
    get_global_capability_registry,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.confluence_connector_repository import (
    ConfluenceConnectorRepository,
)
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.jira_connector_repository import JiraConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.openmetadata_connector_repository import (
    OpenMetadataConnectorRepository,
)
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.postgres.workspace_skill_repository import (
    WorkspaceSkillRepository,
)

from rhetorica_api.app.settings import settings


@lru_cache(maxsize=1)
def get_chat_service_factory() -> ChatServiceFactory:
    execution_service, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    return ChatServiceFactory(
        execution_service=execution_service,
        db_connector_repository=ConnectorRepository(db=db_runtime),
        llm_connector_repository=LlmConnectorRepository(db=db_runtime),
        openmetadata_connector_repository=OpenMetadataConnectorRepository(db=db_runtime),
        rag_connector_repository=RagConnectorRepository(db=db_runtime),
        confluence_connector_repository=ConfluenceConnectorRepository(db=db_runtime),
        jira_connector_repository=JiraConnectorRepository(db=db_runtime),
        workspace_skill_repository=WorkspaceSkillRepository(db=db_runtime),
        chat_repository=ChatRepository(db=db_runtime),
        db=db_runtime,
        capability_registry=get_global_capability_registry(),
    )
