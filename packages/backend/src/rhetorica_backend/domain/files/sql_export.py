"""Deterministic bounded-memory CSV/XLSX export from persisted SQL snapshots."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from io import TextIOWrapper
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, Literal, Protocol

import xlsxwriter
from rhetorica_backend.contracts.files import FileArtifactRef
from rhetorica_backend.domain.execution.snapshots import (
    SqlResultSnapshot,
    SqlResultSnapshotStore,
)
from rhetorica_backend.domain.files.models import ArtifactSource
from rhetorica_backend.domain.files.service import ArtifactNotFoundError, ArtifactService


class SqlResultExportAccessError(PermissionError):
    """The caller cannot export this execution in the requested conversation."""


class SqlResultSnapshotUnavailableError(LookupError):
    """A successful execution has no persisted rows that can be exported."""


class SqlResultExportLimitError(ValueError):
    """The persisted result cannot fit the configured export envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ExecutionSnapshotReader(Protocol):
    def get(self, execution_id: str) -> Any | None: ...

    def get_snapshot(self, execution_id: str) -> SqlResultSnapshot | None: ...


class ExecutionMessageReader(Protocol):
    def get_execution_message(
        self,
        *,
        conversation_id: str,
        execution_id: str,
        owner_user_id: str,
    ) -> Any | None: ...


@dataclass(frozen=True)
class SqlResultExport:
    file: FileArtifactRef
    execution_id: str
    row_count: int
    possibly_truncated: bool


class _BoundedSeekableWriter:
    """Rejects a ZIP write before it can grow beyond the export disk budget."""

    def __init__(self, target: BinaryIO, max_bytes: int) -> None:
        self._target = target
        self._max_bytes = max_bytes
        self._high_water = 0

    def write(self, data: bytes) -> int:
        end = self._target.tell() + len(data)
        if max(self._high_water, end) > self._max_bytes:
            raise SqlResultExportLimitError(
                "sql_export_size_limit",
                "The generated export exceeds the configured file-size limit.",
            )
        written = self._target.write(data)
        self._high_water = max(self._high_water, self._target.tell())
        return written

    def truncate(self, size: int | None = None) -> int:
        target_size = self._target.tell() if size is None else size
        if target_size > self._max_bytes:
            raise SqlResultExportLimitError(
                "sql_export_size_limit",
                "The generated export exceeds the configured file-size limit.",
            )
        return self._target.truncate(size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class SqlResultExportService:
    """Exports persisted guarded rows only; it never re-executes SQL."""

    _XLSX_MAX_DATA_ROWS = 1_048_575
    _XLSX_MAX_STRING_LENGTH = 32_767
    _FORMULA_PREFIXES = ("=", "+", "-", "@")
    _FORMULA_LEADING_WHITESPACE = " \t\r\n"
    _DANGEROUS_CONTROL_PREFIXES = ("\t", "\r", "\n")

    def __init__(
        self,
        *,
        execution_reader: ExecutionSnapshotReader,
        message_reader: ExecutionMessageReader,
        artifact_service: ArtifactService,
        snapshot_store: SqlResultSnapshotStore | None = None,
        max_export_rows: int = _XLSX_MAX_DATA_ROWS,
        max_export_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self._execution_reader = execution_reader
        self._message_reader = message_reader
        self._artifact_service = artifact_service
        self._snapshot_store = snapshot_store
        self._max_export_rows = min(max(1, max_export_rows), self._XLSX_MAX_DATA_ROWS)
        self._max_export_bytes = max_export_bytes

    def export(
        self,
        *,
        execution_id: str,
        export_format: Literal["csv", "xlsx"],
        workspace_id: str,
        conversation_id: str,
        owner_user_id: str,
        allowed_current_execution_ids: set[str] | None = None,
        artifact_id: str | None = None,
    ) -> SqlResultExport:
        current_turn = execution_id in (allowed_current_execution_ids or set())
        linked_message = self._message_reader.get_execution_message(
            conversation_id=conversation_id,
            execution_id=execution_id,
            owner_user_id=owner_user_id,
        )
        if linked_message is None and not current_turn:
            raise SqlResultExportAccessError(
                "The execution is not linked to this conversation or current tool turn."
            )

        history = self._execution_reader.get(execution_id)
        if history is None or history.workspace_id != workspace_id:
            raise SqlResultExportAccessError("Execution workspace ownership mismatch.")
        if history.actor_id != owner_user_id and not (history.actor_id is None and current_turn):
            raise SqlResultExportAccessError("Execution user ownership mismatch.")
        if history.status != "succeeded":
            raise ValueError("Only successful persisted executions can be exported.")

        snapshot = self._get_snapshot(execution_id)
        if snapshot is not None:
            self._validate_snapshot_access(
                snapshot,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
            )
            columns = list(snapshot.columns)
            rows = self._snapshot_rows(snapshot)
            expected_rows = snapshot.row_count
            possibly_truncated = snapshot.possibly_truncated
        else:
            # Compatibility for executions created before snapshot persistence existed.
            columns = list(history.preview.columns)
            legacy_rows = history.preview.rows
            if not legacy_rows and int(history.row_count or 0) > 0:
                raise SqlResultSnapshotUnavailableError(
                    "This execution predates full-result persistence and has no exportable rows. "
                    "Run SQL again with an explicit execution_row_limit, then retry export."
                )
            rows = (list(row) for row in legacy_rows)
            expected_rows = len(legacy_rows)
            applied_limit = int(history.row_limit or 0)
            possibly_truncated = applied_limit > 0 and expected_rows >= applied_limit

        if expected_rows > self._max_export_rows:
            raise SqlResultExportLimitError(
                "sql_export_row_limit",
                f"The result has {expected_rows} rows; the export limit is "
                f"{self._max_export_rows} data rows.",
            )

        if artifact_id:
            try:
                existing = self._artifact_service.get_owned(
                    artifact_id=artifact_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                )
            except ArtifactNotFoundError:
                pass
            else:
                if (
                    existing.conversation_id != conversation_id
                    or existing.workspace_id != workspace_id
                ):
                    raise SqlResultExportAccessError("Export artifact ownership mismatch.")
                return SqlResultExport(
                    self._artifact_service.to_ref(existing),
                    execution_id,
                    expected_rows,
                    possibly_truncated,
                )
        suffix, media_type = (
            ("csv", "text/csv")
            if export_format == "csv"
            else (
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as output:
            bounded_output = _BoundedSeekableWriter(output, self._max_export_bytes)
            try:
                if export_format == "csv":
                    row_count = self._write_csv(
                        bounded_output,
                        columns=columns,
                        rows=rows,
                    )
                else:
                    row_count = self._write_xlsx(
                        bounded_output,
                        columns=columns,
                        rows=rows,
                    )
            except SqlResultExportLimitError:
                raise
            except (
                OSError,
                xlsxwriter.exceptions.FileCreateError,
                xlsxwriter.exceptions.FileSizeError,
            ) as exc:
                raise SqlResultExportLimitError(
                    "sql_export_size_limit",
                    "The export exceeded its bounded temporary-disk or file-size limit.",
                ) from exc
            if row_count != expected_rows:
                raise SqlResultSnapshotUnavailableError(
                    "The persisted SQL snapshot row count does not match its manifest."
                )
            output.seek(0)
            artifact = self._artifact_service.store_generated(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                filename=f"sql-result-{execution_id}.{suffix}",
                declared_media_type=media_type,
                fileobj=output,
                source=ArtifactSource.SQL_RESULT,
                max_size_bytes=self._max_export_bytes,
                **({"artifact_id": artifact_id} if artifact_id else {}),
            )
        return SqlResultExport(
            file=self._artifact_service.to_ref(artifact),
            execution_id=execution_id,
            row_count=row_count,
            possibly_truncated=possibly_truncated,
        )

    def _get_snapshot(self, execution_id: str) -> SqlResultSnapshot | None:
        getter = getattr(self._execution_reader, "get_snapshot", None)
        return getter(execution_id) if callable(getter) else None

    def _snapshot_rows(self, snapshot: SqlResultSnapshot) -> Iterator[list[Any]]:
        if self._snapshot_store is None:
            raise SqlResultSnapshotUnavailableError(
                "The persisted result exists, but snapshot storage is unavailable. Retry later."
            )
        return self._snapshot_store.iter_rows(snapshot)

    @staticmethod
    def _validate_snapshot_access(
        snapshot: SqlResultSnapshot,
        *,
        workspace_id: str,
        conversation_id: str,
        owner_user_id: str,
    ) -> None:
        if (
            snapshot.workspace_id != workspace_id
            or snapshot.conversation_id != conversation_id
            or snapshot.owner_user_id != owner_user_id
        ):
            raise SqlResultExportAccessError("Persisted result ownership mismatch.")

    @classmethod
    def _write_csv(
        cls,
        output: BinaryIO,
        *,
        columns: list[str],
        rows: Iterable[list[Any]],
    ) -> int:
        text = TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
        row_count = 0
        try:
            writer = csv.writer(text)
            writer.writerow([cls._safe_cell(item) for item in columns])
            for row in rows:
                writer.writerow([cls._safe_cell(cell) for cell in row])
                row_count += 1
            text.flush()
            return row_count
        finally:
            text.detach()

    @classmethod
    def _write_xlsx(
        cls,
        output: BinaryIO,
        *,
        columns: list[str],
        rows: Iterable[list[Any]],
    ) -> int:
        workbook = xlsxwriter.Workbook(
            output,
            {
                "constant_memory": True,
                "in_memory": False,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )
        row_count = 0
        try:
            sheet = workbook.add_worksheet("SQL result")
            sheet.write_row(0, 0, [cls._safe_xlsx_cell(item) for item in columns])
            for row_count, row in enumerate(rows, start=1):
                if row_count > cls._XLSX_MAX_DATA_ROWS:
                    raise SqlResultExportLimitError(
                        "sql_export_row_limit",
                        f"XLSX supports at most {cls._XLSX_MAX_DATA_ROWS} data rows "
                        "when the first row contains headers.",
                    )
                sheet.write_row(
                    row_count,
                    0,
                    [cls._safe_xlsx_cell(cell) for cell in row],
                )
        finally:
            workbook.close()
        return row_count

    @classmethod
    def _safe_cell(cls, value: Any) -> Any:
        if isinstance(value, str):
            trimmed = value.lstrip(cls._FORMULA_LEADING_WHITESPACE)
            dangerous = value.startswith(cls._DANGEROUS_CONTROL_PREFIXES) or trimmed.startswith(
                cls._FORMULA_PREFIXES
            )
            return f"'{value}" if dangerous else value
        if value is None or isinstance(value, int | float | bool):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @classmethod
    def _safe_xlsx_cell(cls, value: Any) -> Any:
        safe = cls._safe_cell(value)
        if isinstance(safe, str) and len(safe) > cls._XLSX_MAX_STRING_LENGTH:
            return safe[: cls._XLSX_MAX_STRING_LENGTH]
        return safe
