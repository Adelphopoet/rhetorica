"""Web file tools backed by transport-neutral artifact and sandbox ports."""

from __future__ import annotations

import logging
from tempfile import SpooledTemporaryFile
from time import perf_counter
from typing import Any, ClassVar

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.files.models import ArtifactSource
from rhetorica_backend.domain.files.policy import ArtifactRejectedError
from rhetorica_backend.domain.files.ports import SandboxGateway, SandboxGatewayError
from rhetorica_backend.domain.files.service import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactService,
)
from rhetorica_backend.domain.files.sql_export import (
    SqlResultExportAccessError,
    SqlResultExportLimitError,
    SqlResultExportService,
    SqlResultSnapshotUnavailableError,
)
from rhetorica_backend.domain.files.telemetry import FileTelemetry, NoopFileTelemetry

logger = logging.getLogger(__name__)


class ToolFileCapabilityService:
    """Owns bounded file tool behavior; orchestration only delegates."""

    TOOL_NAMES = (
        "list_files",
        "inspect_file",
        "run_file_code",
        "publish_files",
        "export_sql_result",
    )
    _ERROR_MESSAGES: ClassVar[dict[str, str]] = {
        "file_tools_web_only": "File tools are available only in the web chat transport.",
        "conversation_required": "A conversation is required before using file tools.",
        "invalid_code": "Python code must contain between 1 and 20000 characters.",
        "too_many_files": "At most five input artifacts can be used in one sandbox call.",
        "sandbox_session_required": "Run file code before publishing sandbox outputs.",
        "unknown_sandbox_output": "The requested output ID was not produced in this turn.",
        "invalid_sql_export_request": (
            "Provide a successful current-turn execution ID and format csv or xlsx."
        ),
        "artifact_not_found": "The artifact does not exist or is not owned by the requester.",
        "artifact_access_denied": "The artifact does not belong to this conversation.",
        "sql_execution_access_denied": (
            "The SQL execution is not owned by this user and conversation. Use an execution "
            "ID returned by run_sql in this conversation."
        ),
        "sql_result_snapshot_unavailable": (
            "The full persisted SQL result is unavailable. Run SQL again with an explicit "
            "execution_row_limit, then retry export_sql_result with the new execution ID."
        ),
        "sql_export_row_limit": (
            "The persisted result exceeds the export row limit. Run a narrower SQL query and "
            "export its execution ID."
        ),
        "sql_export_size_limit": (
            "The generated file exceeds the export size limit. Select fewer columns/rows or "
            "use CSV, then retry."
        ),
        "artifact_rejected": "The artifact operation violated an explicit file policy.",
        "sandbox_process_failed": (
            "Python exited with a non-zero status. Inspect stderr and adjust the code."
        ),
        "sandbox_timeout": (
            "Python exceeded the sandbox time limit. Reduce the work per call or process "
            "the file in bounded chunks."
        ),
        "sandbox_oom": (
            "Python exceeded the sandbox memory limit. Use streaming, chunked, or lazy "
            "file processing."
        ),
        "file_processing_unavailable": (
            "File processing is temporarily unavailable; retry the file tool or continue "
            "without file operations."
        ),
        "unknown_file_tool": "The requested file tool is not available.",
    }

    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        sandbox_gateway: SandboxGateway,
        sql_export_service: SqlResultExportService,
        owner_user_id: str,
        telemetry: FileTelemetry | None = None,
    ) -> None:
        self._artifacts = artifact_service
        self._sandbox = sandbox_gateway
        self._sql_exports = sql_export_service
        self._owner_user_id = owner_user_id
        self._telemetry = telemetry or NoopFileTelemetry()
        self._active_session_ids: set[str] = set()

    def execute(
        self,
        *,
        tool_name: str,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        started = perf_counter()
        operation = tool_name if tool_name in self.TOOL_NAMES else "unknown_file_tool"
        outcome = "error"
        bytes_count = 0
        result: dict[str, Any]
        if request.actor_context is None or request.actor_context.source != "web":
            result = self._error("file_tools_web_only")
            outcome = "rejected"
            self._record(operation, outcome, started, bytes_count)
            return result
        if not state.conversation_id:
            result = self._error("conversation_required")
            outcome = "rejected"
            self._record(operation, outcome, started, bytes_count)
            return result
        try:
            if tool_name == "list_files":
                result = self._list_files(state)
            elif tool_name == "inspect_file":
                result = self._inspect_file(arguments, state)
            elif tool_name == "run_file_code":
                result = self._run_code(arguments, state)
            elif tool_name == "publish_files":
                result = self._publish(arguments, request, state)
            elif tool_name == "export_sql_result":
                result = self._export_sql(arguments, request, state)
            else:
                result = self._error("unknown_file_tool")
        except ArtifactNotFoundError:
            result = self._error("artifact_not_found")
            outcome = "rejected"
            return result
        except SqlResultExportAccessError as exc:
            result = self._error("sql_execution_access_denied", detail=str(exc))
            outcome = "rejected"
            return result
        except SqlResultSnapshotUnavailableError as exc:
            result = self._error(
                "sql_result_snapshot_unavailable",
                detail=str(exc),
                retry_recommended=True,
            )
            outcome = "error"
            return result
        except SqlResultExportLimitError as exc:
            result = self._error(exc.code, detail=str(exc))
            outcome = "rejected"
            return result
        except PermissionError:
            result = self._error("artifact_access_denied")
            outcome = "rejected"
            return result
        except (ArtifactConflictError, ArtifactRejectedError) as exc:
            result = self._error(
                "artifact_rejected",
                detail=str(exc),
            )
            outcome = "rejected"
            return result
        except SandboxGatewayError as exc:
            result = self._error(
                exc.code,
                error_message=str(exc),
                retry_recommended=exc.retry_recommended,
            )
            outcome = "error" if exc.retry_recommended else "rejected"
            return result
        except Exception:
            state.add_warning("file_processing_unavailable")
            result = self._error("file_processing_unavailable", retry_recommended=True)
            return result
        else:
            if result.get("timed_out"):
                outcome = "timeout"
            elif result.get("oom_killed"):
                outcome = "oom"
            else:
                outcome = "ok" if result.get("ok") else "rejected"
            bytes_count = self._result_bytes(result)
            return result
        finally:
            self._record(operation, outcome, started, bytes_count)

    def _list_files(self, state: ToolOrchestrationState) -> dict[str, Any]:
        files = self._artifacts.list_conversation(
            conversation_id=str(state.conversation_id),
            owner_user_id=self._owner_user_id,
            limit=100,
        )
        return {
            "ok": True,
            "files": [self._artifacts.to_ref(item).model_dump(mode="json") for item in files],
            "count": len(files),
        }

    def _inspect_file(
        self, arguments: dict[str, Any], state: ToolOrchestrationState
    ) -> dict[str, Any]:
        artifact = self._owned_input(str(arguments.get("artifact_id") or ""), state)
        result = self._sandbox.inspect_file(
            session_id=state.sandbox_session_id,
            input_file=self._sandbox_input(artifact),
        )
        state.sandbox_session_id = str(result.pop("session_id", state.sandbox_session_id or ""))
        if state.sandbox_session_id:
            self._active_session_ids.add(state.sandbox_session_id)
        return {"ok": True, **result}

    def _run_code(self, arguments: dict[str, Any], state: ToolOrchestrationState) -> dict[str, Any]:
        code = str(arguments.get("code") or "")
        if not code or len(code) > 20_000:
            return self._error("invalid_code")
        raw_ids = arguments.get("artifact_ids") or []
        artifact_ids = [str(item) for item in raw_ids if isinstance(item, str)]
        if len(artifact_ids) > 5:
            return self._error("too_many_files")
        artifacts = [self._owned_input(artifact_id, state) for artifact_id in artifact_ids]
        run = self._sandbox.run_python(
            session_id=state.sandbox_session_id,
            code=code,
            inputs=[self._sandbox_input(item) for item in artifacts],
        )
        state.sandbox_session_id = run.session_id
        self._active_session_ids.add(run.session_id)
        state.sandbox_input_artifact_ids = artifact_ids
        state.sandbox_outputs.update({
            item.output_id: {
                "output_id": item.output_id,
                "filename": item.filename,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "source_artifact_ids": list(artifact_ids),
            }
            for item in run.outputs
        })
        if run.timed_out:
            state.add_warning("sandbox_timeout")
        if run.oom_killed:
            state.add_warning("sandbox_oom")
        result: dict[str, Any] = {
            "ok": run.exit_code == 0 and not run.timed_out and not run.oom_killed,
            "exit_code": run.exit_code,
            "stdout": run.stdout[:65536],
            "stderr": run.stderr[:65536],
            "outputs": [
                {
                    "output_id": item.output_id,
                    "filename": item.filename,
                    "media_type": item.media_type,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in run.outputs
            ],
            "timed_out": run.timed_out,
            "oom_killed": run.oom_killed,
            "can_continue": True,
        }
        if run.timed_out:
            result.update(self._error("sandbox_timeout"))
        elif run.oom_killed:
            result.update(self._error("sandbox_oom"))
        elif run.exit_code != 0:
            result.update(self._error("sandbox_process_failed"))
        return result

    def _publish(
        self,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        output_ids = list(dict.fromkeys(str(item) for item in (arguments.get("output_ids") or [])))
        if not output_ids:
            return self._error("unknown_sandbox_output")
        published_artifacts = {item.id: item for item in state.published_files}
        already_published = [
            (
                state.published_sandbox_outputs[output_id]
                if output_id in state.published_sandbox_outputs
                else published_artifacts[output_id]
            )
            for output_id in output_ids
            if output_id in state.published_sandbox_outputs or output_id in published_artifacts
        ]
        pending_output_ids = [
            output_id
            for output_id in output_ids
            if output_id not in state.published_sandbox_outputs
            and output_id not in published_artifacts
        ]
        if not pending_output_ids:
            return {
                "ok": True,
                "files": [item.model_dump(mode="json") for item in already_published],
                "count": 0,
                "already_published": True,
                "requires_publish_files": False,
            }
        if not state.sandbox_session_id:
            return self._error("sandbox_session_required")
        if any(item not in state.sandbox_outputs for item in pending_output_ids):
            return self._error("unknown_sandbox_output")
        published = []
        for output_id in pending_output_ids:
            remaining = self._artifacts.max_session_output_bytes - state.published_output_bytes
            if remaining <= 0:
                raise ValueError("sandbox_session_output_limit")
            with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as output:
                manifest = state.sandbox_outputs[output_id]
                filename, media_type, size_bytes = self._sandbox.download_output(
                    session_id=state.sandbox_session_id,
                    output_id=output_id,
                    destination=output,
                    max_bytes=min(remaining, self._artifacts.max_output_bytes),
                )
                artifact = self._artifacts.store_generated(
                    workspace_id=request.workspace_id,
                    owner_user_id=self._owner_user_id,
                    conversation_id=str(state.conversation_id),
                    filename=filename,
                    declared_media_type=media_type,
                    fileobj=output,
                    source=ArtifactSource.SANDBOX_OUTPUT,
                    source_artifact_ids=list(manifest.get("source_artifact_ids") or []),
                )
            ref = self._artifacts.to_ref(artifact)
            state.published_output_bytes += size_bytes
            state.published_output_ids.add(output_id)
            state.published_sandbox_outputs[output_id] = ref
            state.published_files.append(ref)
            published.append(ref.model_dump(mode="json"))
        return {
            "ok": True,
            "files": [
                *[item.model_dump(mode="json") for item in already_published],
                *published,
            ],
            "count": len(published),
            "already_published": bool(already_published),
            "requires_publish_files": False,
        }

    def close_sessions(self) -> None:
        """Best-effort turn cleanup; supervisor TTL remains the crash fallback."""
        session_ids = tuple(self._active_session_ids)
        self._active_session_ids.clear()
        for session_id in session_ids:
            started = perf_counter()
            outcome = "ok"
            try:
                self._sandbox.close_session(session_id)
            except Exception as exc:
                outcome = "error"
                logger.warning(
                    "sandbox_session_cleanup_failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                self._record("sandbox_cleanup", outcome, started, 0)

    def _export_sql(
        self,
        arguments: dict[str, Any],
        request: ChatAskRequest,
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        execution_id = str(
            arguments.get("execution_id")
            or (state.execution.execution_id if state.execution is not None else "")
        )
        export_format = str(arguments.get("format") or "csv")
        if not execution_id or export_format not in {"csv", "xlsx"}:
            return self._error("invalid_sql_export_request")
        current_ids = {attempt.execution.execution_id for attempt in state.execution_attempts}
        exported = self._sql_exports.export(
            execution_id=execution_id,
            export_format=export_format,  # type: ignore[arg-type]
            workspace_id=request.workspace_id,
            conversation_id=str(state.conversation_id),
            owner_user_id=self._owner_user_id,
            allowed_current_execution_ids=current_ids,
        )
        state.published_files.append(exported.file)
        return {
            "ok": True,
            "file": exported.file.model_dump(mode="json"),
            "execution_id": execution_id,
            "row_count": exported.row_count,
            "possibly_truncated": exported.possibly_truncated,
            "published": True,
            "requires_publish_files": False,
            "delivery_state": "assistant_attachment",
        }

    def _owned_input(self, artifact_id: str, state: ToolOrchestrationState) -> Any:
        artifact = self._artifacts.get_owned(
            artifact_id=artifact_id, owner_user_id=self._owner_user_id
        )
        if artifact.conversation_id != state.conversation_id:
            raise PermissionError("artifact_conversation_mismatch")
        return artifact

    def _sandbox_input(self, artifact: Any) -> Any:
        return self._artifacts.sandbox_input(artifact)

    @classmethod
    def _error(
        cls,
        code: str,
        *,
        detail: str | None = None,
        error_message: str | None = None,
        retry_recommended: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "error_code": code,
            "error_message": error_message
            or cls._ERROR_MESSAGES.get(code, "File tool operation failed."),
            "retry_recommended": retry_recommended,
            "can_continue": True,
        }
        if detail:
            result["validation_errors"] = [detail[:500]]
        return result

    def _record(self, operation: str, outcome: str, started: float, bytes_count: int) -> None:
        self._telemetry.record(
            operation=operation,
            outcome=outcome,
            duration_seconds=perf_counter() - started,
            bytes_count=bytes_count,
        )

    @staticmethod
    def _result_bytes(result: dict[str, Any]) -> int:
        manifests = result.get("outputs") or result.get("files") or []
        if not isinstance(manifests, list):
            return 0
        return sum(int(item.get("size_bytes") or 0) for item in manifests if isinstance(item, dict))
