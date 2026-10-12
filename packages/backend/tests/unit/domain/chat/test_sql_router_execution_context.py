from types import SimpleNamespace

from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
)
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter


def test_sql_router_binds_user_conversation_and_bulk_row_limit() -> None:
    captured = SimpleNamespace(request=None)

    class _ExecutionService:
        def execute(self, request):
            captured.request = request
            return ExecuteSqlResponse(
                execution_id="execution-1",
                status="succeeded",
                engine="clickhouse",
            )

    router = SqlRouter(
        execution_service=_ExecutionService(),  # type: ignore[arg-type]
        db_connector_repository=SimpleNamespace(),
        actor_id="user-1",
    )

    router.execute_sql(
        sql="SELECT * FROM dm.ft_order LIMIT 1000",
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="clickhouse",
            port=8123,
            username="default",
            database="default",
        ),
        workspace_id="default",
        dialect="clickhouse",
        row_limit=1_048_575,
        conversation_id="conversation-1",
    )

    assert captured.request.actor_id == "user-1"
    assert captured.request.conversation_id == "conversation-1"
    assert captured.request.policy.row_limit == 1_048_575
