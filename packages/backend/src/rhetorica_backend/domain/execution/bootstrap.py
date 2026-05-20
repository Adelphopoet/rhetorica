from rhetorica_backend.domain.execution.service import ExecutionService
from rhetorica_backend.infra.engine_clickhouse.adapter import ClickHouseAdapter
from rhetorica_backend.infra.engine_greenplum.adapter import GreenplumAdapter
from rhetorica_backend.infra.engine_impala.adapter import ImpalaAdapter
from rhetorica_backend.infra.engine_trino.adapter import TrinoAdapter
from rhetorica_backend.infra.postgres.execution_history_repository import ExecutionHistoryRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def build_execution_service(database_url: str) -> tuple[ExecutionService, DatabaseRuntime]:
    db_runtime = DatabaseRuntime(database_url)
    history_repository = ExecutionHistoryRepository(db=db_runtime)
    service = ExecutionService(
        history_repository=history_repository,
        adapters={
            "clickhouse": ClickHouseAdapter(),
            "greenplum": GreenplumAdapter(),
            "impala": ImpalaAdapter(),
            "trino": TrinoAdapter(),
        },
    )
    return service, db_runtime
