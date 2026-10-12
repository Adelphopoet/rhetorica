"""Ports and contracts for bounded-memory persisted SQL result snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class SqlResultSnapshot:
    execution_id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str
    storage_key: str
    columns: tuple[str, ...]
    row_count: int
    size_bytes: int
    sha256: str
    possibly_truncated: bool
    created_at: datetime


class SqlResultSnapshotCapture(Protocol):
    def start(self, columns: Iterable[str]) -> None: ...

    def write_rows(self, rows: Iterable[Iterable[Any]]) -> None: ...

    def finish(self, *, possibly_truncated: bool) -> SqlResultSnapshot: ...

    def abort(self) -> None: ...


class SqlResultSnapshotStore(Protocol):
    def begin(
        self,
        *,
        execution_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
    ) -> SqlResultSnapshotCapture: ...

    def iter_rows(self, snapshot: SqlResultSnapshot) -> Iterator[list[Any]]: ...

    def delete(self, snapshot: SqlResultSnapshot) -> None: ...
