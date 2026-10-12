from fastapi import APIRouter, HTTPException, Query
from rhetorica_backend.contracts import (
    CreateScenarioRequest,
    ScenarioDefinitionItem,
    ScenarioRunRequest,
    ScenarioRunResponse,
    UpdateScenarioRequest,
)
from rhetorica_backend.domain.scenarios import ScenarioCatalogService
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.scenario_authoring_repository import (
    ScenarioAuthoringRepository,
)
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository

from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.identity import get_db_runtime, resolve_current_user

router = APIRouter(prefix="/v1/scenarios", tags=["scenarios"])


def _service() -> ScenarioCatalogService:
    db_runtime = get_db_runtime()
    return ScenarioCatalogService(
        scenario_repository=ScenarioRepository(db=db_runtime),
        chat_repository=ChatRepository(db=db_runtime),
        chat_factory=get_chat_service_factory(),
        db_connector_repository=ConnectorRepository(db=db_runtime),
        llm_connector_repository=LlmConnectorRepository(db=db_runtime),
    )


@router.get("", response_model=list[ScenarioDefinitionItem])
def list_scenarios(workspace_id: str = Query(default="default", min_length=1, max_length=128)):
    current_user = resolve_current_user(workspace_id)
    definitions = _service().list_definitions(workspace_id=workspace_id, current_user=current_user)
    authoring = ScenarioAuthoringRepository(get_db_runtime())
    for definition in definitions:
        definition.execution_kind = (
            "workflow" if authoring.has_workflow(definition.id) else "legacy"
        )
    return definitions


@router.post("", response_model=ScenarioDefinitionItem)
def create_scenario(payload: CreateScenarioRequest) -> ScenarioDefinitionItem:
    current_user = resolve_current_user(payload.workspace_id)
    try:
        return _service().create_definition(payload, current_user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{scenario_id}", response_model=ScenarioDefinitionItem)
def update_scenario(scenario_id: str, payload: UpdateScenarioRequest) -> ScenarioDefinitionItem:
    current_user = resolve_current_user(payload.workspace_id)
    authoring = ScenarioAuthoringRepository(get_db_runtime())
    authoring.get_draft(scenario_id, current_user)
    if authoring.has_workflow(scenario_id):
        raise HTTPException(status_code=409, detail="workflow_requires_draft_api")
    try:
        return _service().update_definition(
            scenario_id=scenario_id,
            request=payload,
            current_user=current_user,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.delete("/{scenario_id}", response_model=ScenarioDefinitionItem)
def archive_scenario(
    scenario_id: str,
    workspace_id: str = Query(default="default", min_length=1, max_length=128),
) -> ScenarioDefinitionItem:
    current_user = resolve_current_user(workspace_id)
    try:
        return _service().archive_definition(
            scenario_id=scenario_id,
            workspace_id=workspace_id,
            current_user=current_user,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/{scenario_id}/run", response_model=ScenarioRunResponse)
def run_scenario(scenario_id: str, payload: ScenarioRunRequest) -> ScenarioRunResponse:
    current_user = resolve_current_user(payload.workspace_id)
    authoring = ScenarioAuthoringRepository(get_db_runtime())
    authoring.list_versions(scenario_id, current_user)
    if authoring.has_workflow(scenario_id):
        raise HTTPException(status_code=409, detail="workflow_requires_async_runs_api")
    try:
        return _service().run_definition(
            scenario_id=scenario_id,
            request=payload,
            current_user=current_user,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
