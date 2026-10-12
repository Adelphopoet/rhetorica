"""Gzip JSONL SQL snapshots stored in S3/MinIO with bounded local memory."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections.abc import Iterable, Iterator
from contextlib import suppress
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from ipaddress import IPv4Address, IPv6Address
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO
from uuid import UUID

from rhetorica_backend.domain.execution.snapshots import SqlResultSnapshot
from rhetorica_backend.domain.files.ports import ObjectStorage


class SqlResultSnapshotLimitError(ValueError):
    pass


class ObjectStorageSqlResultSnapshotStore:
    """Provider-neutral snapshot format; S3 is hidden behind ObjectStorage."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        max_rows: int = 1_048_575,
        max_bytes: int = 512 * 1024 * 1024,
        max_cell_bytes: int = 1024 * 1024,
        max_row_bytes: int = 16 * 1024 * 1024,
        spool_memory_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._storage = storage
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._max_cell_bytes = min(max_cell_bytes, max_bytes)
        self._max_row_bytes = min(max_row_bytes, max_bytes)
        self._spool_memory_bytes = spool_memory_bytes

    def begin(
        self,
        *,
        execution_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
    ) -> _Capture:
        storage_key = (
            f"workspaces/{workspace_id}/users/{owner_user_id}/"
            f"conversations/{conversation_id}/sql-snapshots/{execution_id}.jsonl.gz"
        )
        return _Capture(
            storage=self._storage,
            execution_id=execution_id,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            storage_key=storage_key,
            max_rows=self._max_rows,
            max_bytes=self._max_bytes,
            max_cell_bytes=self._max_cell_bytes,
            max_row_bytes=self._max_row_bytes,
            spool_memory_bytes=self._spool_memory_bytes,
        )

    def iter_rows(self, snapshot: SqlResultSnapshot) -> Iterator[list[Any]]:
        stored = self._storage.open(snapshot.storage_key)
        try:
            with gzip.GzipFile(fileobj=stored.body, mode="rb") as compressed:
                for raw_line in compressed:
                    payload = json.loads(raw_line)
                    if not isinstance(payload, list):
                        raise TypeError("invalid_sql_result_snapshot_row")
                    yield payload
        finally:
            stored.body.close()

    def delete(self, snapshot: SqlResultSnapshot) -> None:
        self._storage.delete(snapshot.storage_key)


class _Capture:
    _HASH_CHUNK_BYTES = 1024 * 1024
    _MAX_CONTAINER_ITEMS = 100_000
    _MAX_NESTING_DEPTH = 20
    _MAX_ROW_CELLS = 10_000

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        execution_id: str,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        storage_key: str,
        max_rows: int,
        max_bytes: int,
        max_cell_bytes: int,
        max_row_bytes: int,
        spool_memory_bytes: int,
    ) -> None:
        self._storage = storage
        self._execution_id = execution_id
        self._workspace_id = workspace_id
        self._owner_user_id = owner_user_id
        self._conversation_id = conversation_id
        self._storage_key = storage_key
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._max_cell_bytes = max_cell_bytes
        self._max_row_bytes = max_row_bytes
        # The capture owns this handle across start/write/finish and closes it there.
        self._buffer: BinaryIO = SpooledTemporaryFile(  # noqa: SIM115
            max_size=spool_memory_bytes,
            mode="w+b",
        )
        self._compressed = gzip.GzipFile(fileobj=self._buffer, mode="wb", mtime=0)
        self._columns: tuple[str, ...] = ()
        self._row_count = 0
        self._uncompressed_bytes = 0
        self._closed = False

    def start(self, columns: Iterable[str]) -> None:
        if self._columns:
            return
        bounded_columns: list[str] = []
        columns_bytes = 2
        for column_count, column in enumerate(columns, start=1):
            if column_count > self._MAX_ROW_CELLS:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_row_cell_limit")
            column_name = str(column)
            column_bytes = self._bounded_json_string_size(
                column_name,
                limit=self._max_cell_bytes,
            )
            columns_bytes += column_bytes + (1 if bounded_columns else 0)
            if columns_bytes > self._max_row_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_row_size_limit")
            bounded_columns.append(column_name)
        self._columns = tuple(bounded_columns)

    def write_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        if self._closed:
            return
        for row in rows:
            if self._row_count >= self._max_rows:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_row_limit")
            payload = self._encode_row(row)
            uncompressed_bytes = len(payload) + 1
            if self._uncompressed_bytes + uncompressed_bytes > self._max_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_size_limit")
            self._compressed.write(payload)
            self._compressed.write(b"\n")
            self._uncompressed_bytes += uncompressed_bytes
            self._row_count += 1
        self._compressed.flush()
        if self._buffer.tell() > self._max_bytes:
            raise SqlResultSnapshotLimitError("sql_result_snapshot_size_limit")

    def finish(self, *, possibly_truncated: bool) -> SqlResultSnapshot:
        if self._closed:
            raise RuntimeError("sql_result_snapshot_capture_closed")
        self._compressed.close()
        self._closed = True
        uploaded = False
        try:
            size_bytes, sha256 = self._measure(self._buffer)
            self._validate_size(size_bytes)
            self._buffer.seek(0)
            self._storage.upload_fileobj(
                key=self._storage_key,
                fileobj=self._buffer,
                size_bytes=size_bytes,
                media_type="application/gzip",
            )
            uploaded = True
            return SqlResultSnapshot(
                execution_id=self._execution_id,
                workspace_id=self._workspace_id,
                owner_user_id=self._owner_user_id,
                conversation_id=self._conversation_id,
                storage_key=self._storage_key,
                columns=self._columns,
                row_count=self._row_count,
                size_bytes=size_bytes,
                sha256=sha256,
                possibly_truncated=possibly_truncated,
                created_at=datetime.now(tz=UTC),
            )
        except Exception:
            if uploaded:
                with suppress(Exception):
                    self._storage.delete(self._storage_key)
            raise
        finally:
            self._buffer.close()

    def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._compressed.close()
        self._buffer.close()

    @classmethod
    def _measure(cls, fileobj: BinaryIO) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        fileobj.seek(0)
        while chunk := fileobj.read(cls._HASH_CHUNK_BYTES):
            size_bytes += len(chunk)
            digest.update(chunk)
        fileobj.seek(0)
        return size_bytes, digest.hexdigest()

    def _validate_size(self, size_bytes: int) -> None:
        if size_bytes > self._max_bytes:
            raise SqlResultSnapshotLimitError("sql_result_snapshot_size_limit")

    def _encode_row(self, row: Iterable[Any]) -> bytes:
        encoded_cells: list[bytes] = []
        row_bytes = 2
        for cell_count, cell in enumerate(row, start=1):
            if cell_count > self._MAX_ROW_CELLS:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_row_cell_limit")
            normalized = self._json_cell(cell)
            self._bounded_json_size(normalized, limit=self._max_cell_bytes)
            encoded = json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > self._max_cell_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
            row_bytes += len(encoded) + (1 if encoded_cells else 0)
            if row_bytes > self._max_row_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_row_size_limit")
            encoded_cells.append(encoded)
        return b"[" + b",".join(encoded_cells) + b"]"

    def _bounded_json_size(self, value: Any, *, limit: int, depth: int = 0) -> int:
        if depth > self._MAX_NESTING_DEPTH:
            raise SqlResultSnapshotLimitError("sql_result_snapshot_nesting_limit")
        if value is None:
            return self._accept_size(4, limit)
        if value is True:
            return self._accept_size(4, limit)
        if value is False:
            return self._accept_size(5, limit)
        if isinstance(value, str):
            return self._bounded_json_string_size(value, limit=limit)
        if isinstance(value, int | float):
            return self._accept_size(len(str(value).encode("ascii")), limit)
        if isinstance(value, list | tuple):
            if len(value) > self._MAX_CONTAINER_ITEMS:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_container_limit")
            size = 2
            for index, item in enumerate(value):
                remaining = limit - size - (1 if index else 0)
                if remaining < 0:
                    raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
                size += (1 if index else 0) + self._bounded_json_size(
                    item,
                    limit=remaining,
                    depth=depth + 1,
                )
            return self._accept_size(size, limit)
        if isinstance(value, dict):
            if len(value) > self._MAX_CONTAINER_ITEMS:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_container_limit")
            size = 2
            for index, (key, item) in enumerate(value.items()):
                if not isinstance(key, str | int | float | bool) and key is not None:
                    raise TypeError("invalid_sql_result_snapshot_key")
                if key is None:
                    key_text = "null"
                elif isinstance(key, bool):
                    key_text = str(key).lower()
                else:
                    key_text = str(key)
                remaining = limit - size - (1 if index else 0)
                key_size = self._bounded_json_string_size(key_text, limit=max(0, remaining))
                remaining -= key_size + 1
                if remaining < 0:
                    raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
                value_size = self._bounded_json_size(
                    item,
                    limit=remaining,
                    depth=depth + 1,
                )
                size += (1 if index else 0) + key_size + 1 + value_size
            return self._accept_size(size, limit)
        raise TypeError("invalid_sql_result_snapshot_value")

    def _bounded_json_string_size(self, value: str, *, limit: int) -> int:
        size = 2
        for character in value:
            codepoint = ord(character)
            if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
                size += 2
            elif codepoint < 0x20:
                size += 6
            else:
                size += len(character.encode("utf-8"))
            if size > limit:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
        return size

    @staticmethod
    def _accept_size(size: int, limit: int) -> int:
        if size > limit:
            raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
        return size

    def _json_cell(self, value: Any) -> Any:
        if value is None or isinstance(value, str | int | bool):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, list | tuple | dict):
            return value
        if isinstance(value, bytes | bytearray | memoryview):
            raw = bytes(value)
            if len(raw) * 4 + 3 > self._max_cell_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
            return str(raw)
        if isinstance(value, Decimal):
            if len(value.as_tuple().digits) > self._max_cell_bytes:
                raise SqlResultSnapshotLimitError("sql_result_snapshot_cell_size_limit")
            return str(value)
        if isinstance(value, datetime | date | time):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, IPv4Address | IPv6Address):
            return str(value)
        if isinstance(value, Enum):
            return self._json_cell(value.value)
        raise TypeError("unsupported_sql_result_snapshot_cell")
