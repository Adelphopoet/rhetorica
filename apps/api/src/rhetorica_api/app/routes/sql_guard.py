from fastapi import APIRouter
from rhetorica_backend.contracts import SqlGuardRequest, SqlGuardResponse
from rhetorica_backend.sql_guard.service import SqlGuardService

router = APIRouter(prefix="/v1/sql", tags=["sql"])
_service = SqlGuardService()


@router.post("/guard", response_model=SqlGuardResponse)
def guard_sql(payload: SqlGuardRequest) -> SqlGuardResponse:
    return _service.guard(payload)
