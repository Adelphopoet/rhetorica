"""Internal HTTP sandbox gateway shared by Docker and Kubernetes deployments."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from pathlib import PurePath
from typing import BinaryIO
from urllib.parse import quote, unquote

import httpx
from rhetorica_backend.domain.files.models import (
    SandboxInput,
    SandboxOutput,
    SandboxRunResult,
)
from rhetorica_backend.domain.files.ports import SandboxGatewayError


class HttpSandboxGateway:
    """Stable control-plane protocol; runtime implementation stays replaceable."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_token: str,
        timeout_seconds: float = 75.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            headers={"X-Rhetorica-Sandbox-Token": internal_token},
            transport=transport,
        )

    def run_python(
        self,
        *,
        session_id: str | None,
        code: str,
        inputs: Iterable[SandboxInput],
        operation_id: str | None = None,
    ) -> SandboxRunResult:
        resolved_session = session_id or self._create_session()
        try:
            response = self._request(
                "POST",
                f"/internal/v1/sessions/{resolved_session}/execute",
                json={
                    "code": code,
                    "inputs": [self._input_payload(item) for item in inputs],
                    **({"operation_id": operation_id} if operation_id else {}),
                },
            )
        except SandboxGatewayError as exc:
            if exc.code == "sandbox_timeout":
                self._cancel_timed_out_session(resolved_session)
            raise
        payload = response.json()
        return SandboxRunResult(
            session_id=resolved_session,
            exit_code=int(payload.get("exit_code", 1)),
            stdout=str(payload.get("stdout") or "")[:65536],
            stderr=str(payload.get("stderr") or "")[:65536],
            outputs=tuple(
                SandboxOutput(
                    output_id=str(item["output_id"]),
                    filename=str(item["filename"]),
                    media_type=str(item.get("media_type") or "application/octet-stream"),
                    size_bytes=int(item.get("size_bytes") or 0),
                    sha256=str(item.get("sha256") or ""),
                )
                for item in payload.get("outputs", [])
                if isinstance(item, dict)
            ),
            timed_out=bool(payload.get("timed_out", False)),
            oom_killed=bool(payload.get("oom_killed", False)),
        )

    def ensure_session(self, session_id: str) -> str:
        response = self._request("PUT", f"/internal/v1/sessions/{session_id}")
        return str(response.json()["session_id"])

    def inspect_file(
        self,
        *,
        session_id: str | None,
        input_file: SandboxInput,
    ) -> dict[str, object]:
        resolved_session = session_id or self._create_session()
        try:
            response = self._request(
                "POST",
                f"/internal/v1/sessions/{resolved_session}/inspect",
                json={"input": self._input_payload(input_file)},
            )
        except SandboxGatewayError as exc:
            if exc.code == "sandbox_timeout":
                self._cancel_timed_out_session(resolved_session)
            raise
        payload = response.json()
        payload["session_id"] = resolved_session
        return payload

    def close_session(self, session_id: str) -> None:
        self._request("DELETE", f"/internal/v1/sessions/{session_id}")

    def download_output(
        self,
        *,
        session_id: str,
        output_id: str,
        destination: BinaryIO,
        max_bytes: int,
    ) -> tuple[str, str, int]:
        total = 0
        try:
            with self._client.stream(
                "GET",
                f"/internal/v1/sessions/{quote(session_id, safe='')}/outputs/{quote(output_id, safe='')}",
            ) as response:
                self._raise_for_status(response)
                filename = PurePath(
                    unquote(response.headers.get("X-Artifact-Filename", "output.bin"))
                ).name
                media_type = response.headers.get("Content-Type", "application/octet-stream")
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    total += len(chunk)
                    self._validate_output_size(total=total, max_bytes=max_bytes)
                    destination.write(chunk)
        except SandboxGatewayError:
            raise
        except httpx.TimeoutException as exc:
            raise SandboxGatewayError(
                "sandbox_timeout",
                "Sandbox request exceeded its time limit.",
                retry_recommended=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SandboxGatewayError(
                "sandbox_unavailable",
                "Sandbox service is unavailable.",
                retry_recommended=True,
            ) from exc
        destination.seek(0)
        return filename, media_type, total

    @staticmethod
    def _validate_output_size(*, total: int, max_bytes: int) -> None:
        if total > max_bytes:
            raise SandboxGatewayError(
                "sandbox_output_too_large",
                "Sandbox output exceeds the configured publication limit.",
            )

    def _create_session(self) -> str:
        response = self._request("POST", "/internal/v1/sessions")
        return str(response.json()["session_id"])

    def _cancel_timed_out_session(self, session_id: str) -> None:
        """Request supervisor cancellation; its DELETE kills any active jail."""
        with suppress(Exception):
            self._client.delete(
                f"/internal/v1/sessions/{quote(session_id, safe='')}",
                timeout=5.0,
            )

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SandboxGatewayError(
                "sandbox_timeout",
                "Sandbox request exceeded its time limit.",
                retry_recommended=True,
            ) from exc
        except httpx.RequestError as exc:
            raise SandboxGatewayError(
                "sandbox_unavailable",
                "Sandbox service is unavailable.",
                retry_recommended=True,
            ) from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if not response.is_error:
            return
        detail: object = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
        except ValueError:
            detail = None
        if response.status_code == 422:
            reason = detail if isinstance(detail, str) and detail else "operation_rejected"
            raise SandboxGatewayError(
                f"sandbox_{reason}",
                f"Sandbox rejected the operation: {reason}.",
            )
        if response.status_code == 404:
            raise SandboxGatewayError(
                "sandbox_resource_not_found",
                "Sandbox session or output no longer exists; start a new sandbox session.",
                retry_recommended=True,
            )
        if response.status_code == 429:
            raise SandboxGatewayError(
                "sandbox_busy",
                "Sandbox concurrency is exhausted; retry the operation later.",
                retry_recommended=True,
            )
        raise SandboxGatewayError(
            "sandbox_unavailable",
            "Sandbox service returned an internal error.",
            retry_recommended=response.status_code >= 500,
        )

    @staticmethod
    def _input_payload(item: SandboxInput) -> dict[str, str]:
        return {
            "artifact_id": item.artifact_id,
            "filename": item.filename,
            "download_url": item.download_url,
            "sha256": item.sha256,
        }
