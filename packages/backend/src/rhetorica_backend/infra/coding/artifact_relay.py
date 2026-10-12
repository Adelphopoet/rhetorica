from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
from uuid import uuid4

from rhetorica_backend.infra.object_storage import S3ObjectStorage


class SignedS3CodingArtifactRelay:
    """Opaque, scope-bound artifact references persisted outside runner Pods."""

    def __init__(self, *, storage: S3ObjectStorage, signing_secret: str) -> None:
        if not signing_secret:
            raise ValueError("coding_artifact_signing_secret_required")
        self._storage = storage
        self._secret = signing_secret.encode()

    def persist(
        self,
        *,
        payload: bytes,
        digest: str,
        workspace_id: str,
        owner_user_id: str,
        run_id: str,
        task_id: str,
        kind: str,
    ) -> str:
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, digest):
            raise ValueError("coding_artifact_digest_mismatch")
        artifact_uuid = str(uuid4())
        key = "/".join([
            "coding",
            self._safe(workspace_id),
            self._safe(owner_user_id),
            self._safe(run_id),
            self._safe(task_id),
            self._safe(kind),
            f"{artifact_uuid}.tar.gz",
        ])
        self._storage.upload_fileobj(
            key=key,
            fileobj=io.BytesIO(payload),
            size_bytes=len(payload),
            media_type="application/gzip",
        )
        return self._encode({
            "v": 1,
            "id": artifact_uuid,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "run_id": run_id,
            "task_id": task_id,
            "kind": kind,
            "key": key,
            "digest": digest,
        })

    def download_owned(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        owner_user_id: str,
        run_id: str,
        task_id: str,
    ) -> tuple[bytes, str]:
        record = self._decode(artifact_id)
        if (
            record.get("workspace_id") != workspace_id
            or record.get("owner_user_id") != owner_user_id
            or record.get("run_id") != run_id
            or record.get("task_id") != task_id
        ):
            raise PermissionError("coding_artifact_scope_mismatch")
        stored = self._storage.open(str(record["key"]))
        payload = stored.body.read()
        digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(digest, str(record.get("digest", ""))):
            raise ValueError("coding_artifact_digest_mismatch")
        return payload, digest

    def scope(self, artifact_id: str) -> dict[str, object]:
        """Validate a signed reference before inspecting its server-owned scope."""
        return self._decode(artifact_id)

    def _encode(self, record: dict[str, object]) -> str:
        body = (
            base64.urlsafe_b64encode(
                json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"ca1.{body}.{signature}"

    def _decode(self, value: str) -> dict[str, object]:
        try:
            prefix, body, signature = value.split(".", 2)
            expected = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
            padding = "=" * (-len(body) % 4)
            record = json.loads(base64.urlsafe_b64decode(body + padding))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("coding_artifact_reference_invalid") from exc
        if (
            prefix != "ca1"
            or not hmac.compare_digest(expected, signature)
            or not isinstance(record, dict)
            or record.get("v") != 1
        ):
            raise ValueError("coding_artifact_reference_invalid")
        return record

    @staticmethod
    def _safe(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:32]
