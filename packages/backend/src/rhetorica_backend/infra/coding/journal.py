"""Bounded durable SDK/tool outcomes; never part of Temporal history."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

from botocore.exceptions import ClientError
from rhetorica_backend.domain.files.ports import ObjectStorage


class CodingOperationJournal:
    def __init__(self, storage: ObjectStorage, *, max_bytes: int = 8 * 1024 * 1024):
        self._storage = storage
        self._max_bytes = max_bytes

    @staticmethod
    def _key(*, run_id: str, task_id: str, operation_id: str) -> str:
        digest = hashlib.sha256(operation_id.encode()).hexdigest()
        return f"coding-journal/{run_id}/{task_id}/{digest}.json"

    def get(self, *, run_id: str, task_id: str, operation_id: str) -> dict[str, Any] | None:
        key = self._key(run_id=run_id, task_id=task_id, operation_id=operation_id)
        try:
            stored = self._storage.open(key)
        except FileNotFoundError:
            return None
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        try:
            data = stored.body.read(self._max_bytes + 1)
            if len(data) > self._max_bytes:
                raise ValueError("coding_journal_output_limit")
            return json.loads(data)
        finally:
            stored.body.close()

    def put(self, *, run_id: str, task_id: str, operation_id: str, value: dict[str, Any]) -> None:
        body = json.dumps(value, sort_keys=True).encode()
        if len(body) > self._max_bytes:
            raise ValueError("coding_journal_output_limit")
        self._storage.upload_fileobj(
            key=self._key(run_id=run_id, task_id=task_id, operation_id=operation_id),
            fileobj=io.BytesIO(body),
            size_bytes=len(body),
            media_type="application/json",
        )
