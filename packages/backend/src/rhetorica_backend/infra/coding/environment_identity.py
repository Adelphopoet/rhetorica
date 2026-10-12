"""Deployment-owned tool manifest; no user-supplied runtime identity."""

import hashlib
import json
from pathlib import Path

import httpx


class CodingEnvironmentIdentity:
    def __init__(
        self,
        *,
        manifest_digest: str,
        runtime_url: str | None = None,
        manifest_path: str | None = None,
    ):
        self.digest, self.url, self.path = manifest_digest, runtime_url, manifest_path

    def tool_versions(self) -> dict[str, str]:
        if self.url:
            response = httpx.get(self.url.rstrip("/") + "/healthz", timeout=10)
            response.raise_for_status()
            health = response.json()
            if health["software_manifest_digest"] != self.digest:
                raise ValueError("coding_environment_manifest_mismatch")
            return health["tool_versions"]
        if not self.path:
            raise ValueError("coding_environment_tool_manifest_not_configured")
        payload = Path(self.path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != self.digest:
            raise ValueError("coding_environment_manifest_mismatch")
        return json.loads(payload)
