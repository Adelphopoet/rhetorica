"""Bounded JSON manifests in the existing object store, never in workflow history."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from rhetorica_backend.domain.files.ports import ObjectStorage
from rhetorica_backend.domain.scenarios.errors import WorkflowError


class ScenarioDataStore:
    def __init__(self, storage: ObjectStorage, max_json_bytes: int = 1024 * 1024):
        self.storage = storage
        self.max_json_bytes = max_json_bytes

    def put(
        self, *, run_id: str, node_id: str, attempt: int, role: str, value: dict[str, Any]
    ) -> str:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > self.max_json_bytes:
            raise WorkflowError("scenario_json_limit", "Сохраните объёмные данные в файл.")
        key = f"scenario-runs/{run_id}/{node_id}/{attempt}/{role}.json"
        self.storage.upload_fileobj(
            key=key, fileobj=BytesIO(body), size_bytes=len(body), media_type="application/json"
        )
        return key

    def get(self, ref: str, *, run_id: str) -> dict[str, Any]:
        if not ref.startswith(f"scenario-runs/{run_id}/"):
            raise WorkflowError("scenario_data_access_denied")
        stored = self.storage.open(ref)
        try:
            if stored.size_bytes > self.max_json_bytes:
                raise WorkflowError("scenario_json_limit")
            body = stored.body.read(self.max_json_bytes + 1)
            if len(body) > self.max_json_bytes:
                raise WorkflowError("scenario_json_limit")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise WorkflowError("invalid_scenario_manifest")
            return value
        finally:
            stored.body.close()

    def committed(self, run_id: str, node_id: str) -> dict[str, Any] | None:
        return self.optional(f"scenario-runs/{run_id}/{node_id}/0/committed.json", run_id=run_id)

    def optional(self, ref: str, *, run_id: str) -> dict[str, Any] | None:
        try:
            return self.get(ref, run_id=run_id)
        except FileNotFoundError:
            return None
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) not in {
                "NoSuchKey",
                "404",
                "NotFound",
            }:
                raise
            return None
