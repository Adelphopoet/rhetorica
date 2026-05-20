from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rhetorica_backend.contracts.execution import EngineConnectionConfig


@dataclass(frozen=True)
class EngineRunRequest:
    execution_id: str
    sql: str
    timeout_ms: int
    row_limit: int


@dataclass(frozen=True)
class EngineRunResult:
    query_id: str | None
    columns: list[str]
    rows: list[list[object]]
    row_count: int


class ActiveExecution(Protocol):
    query_id: str | None

    def fetch(self) -> EngineRunResult: ...

    def cancel(self) -> None: ...

    def close(self) -> None: ...


class EngineAdapter(Protocol):
    def start(
        self, connection: EngineConnectionConfig, request: EngineRunRequest
    ) -> ActiveExecution: ...
