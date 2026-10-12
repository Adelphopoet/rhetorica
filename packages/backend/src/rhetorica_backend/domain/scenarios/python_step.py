"""Isolated Python operations with durable input/output files and replayable jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import replace
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, cast
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.scenario_workflows import PythonNode
from rhetorica_backend.domain.files.models import ArtifactSource, SandboxInput
from rhetorica_backend.domain.files.ports import SandboxGateway
from rhetorica_backend.domain.files.service import ArtifactService
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository

INPUT_MANIFEST = "rhetorica-inputs.json"
OUTPUT_MANIFEST = "rhetorica-result.json"


def wrap_python(code: str) -> str:
    # No interpolation of values into source. The JSON input manifest is read in jail.
    return (
        "import json as _rh_json\n"
        f"with open('/workspace/input/{INPUT_MANIFEST}', encoding='utf-8') as _rh_file:\n"
        "    inputs = _rh_json.load(_rh_file)\n"
        "result = None\n" + code + "\n"
        f"with open('/workspace/output/{OUTPUT_MANIFEST}', 'w', encoding='utf-8') as _rh_file:\n"
        "    _rh_json.dump({'result': result}, _rh_file, ensure_ascii=False, allow_nan=False)\n"
    )


class ScenarioPythonStepRunner:
    def __init__(
        self,
        sandbox: SandboxGateway,
        artifacts: ArtifactService,
        data: ScenarioDataStore,
        repository: ScenarioActivityRepository,
    ):
        self.sandbox = sandbox
        self.artifacts = artifacts
        self.data = data
        self.repository = repository

    async def run(
        self, node: PythonNode, inputs: dict[str, Any], context: ScenarioStepContext
    ) -> dict[str, Any]:
        task = asyncio.create_task(asyncio.to_thread(self._execute, node, inputs, context))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with suppress(Exception):
                await asyncio.to_thread(self.sandbox.close_session, context.operation_id)
            with suppress(Exception):
                await task
            raise

    def _inputs(self, inputs: dict[str, Any], context: ScenarioStepContext) -> list[SandboxInput]:
        files: dict[str, SandboxInput] = {}

        def materialize(value: Any) -> Any:
            if isinstance(value, dict):
                if isinstance(value.get("id"), str) and "filename" in value:
                    artifact = self.artifacts.get_owned(
                        artifact_id=value["id"],
                        owner_user_id=context.user.id,
                        workspace_id=context.user.workspace_id,
                    )
                    self.repository.pin_artifact(
                        context.run_id, artifact.id, context.node_id, role="input"
                    )
                    filename = f"{artifact.id}_{artifact.filename[-200:]}"
                    files[artifact.id] = replace(
                        self.artifacts.sandbox_input(artifact), filename=filename
                    )
                    return f"/workspace/input/{filename}"
                return {name: materialize(item) for name, item in value.items()}
            if isinstance(value, list):
                return [materialize(item) for item in value]
            return value

        values = materialize(inputs)
        if len(files) > 5:
            raise WorkflowError(
                "python_input_file_limit", "Один Python-шаг принимает до пяти файлов."
            )
        ref = self.data.put(
            run_id=context.run_id,
            node_id=context.node_id,
            attempt=context.attempt,
            role="python-inputs",
            value=values,
        )
        body = json.dumps(values, ensure_ascii=False, allow_nan=False).encode()
        manifest = SandboxInput(
            artifact_id=str(uuid5(NAMESPACE_URL, f"{context.operation_id}/inputs")),
            filename=INPUT_MANIFEST,
            download_url=self.data.storage.create_download_url(ref),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        return [*files.values(), manifest]

    def _execute(
        self, node: PythonNode, inputs: dict[str, Any], context: ScenarioStepContext
    ) -> dict[str, Any]:
        role = f"python-{context.operation_id}"
        recovered = self.data.optional(
            f"scenario-runs/{context.run_id}/{context.node_id}/0/{role}.json",
            run_id=context.run_id,
        )
        if recovered is None:
            recovered = self._publish(node, inputs, context)
            self.repository.commit_operation_result(
                context.run_id, context.node_id, context.attempt, self.data, role, recovered
            )
        # A retry after publication (including a failed close) needs only the durable result.
        self.sandbox.close_session(context.operation_id)
        return recovered

    def _publish(
        self, node: PythonNode, inputs: dict[str, Any], context: ScenarioStepContext
    ) -> dict[str, Any]:
        inputs_to_sandbox = self._inputs(inputs, context)
        self.sandbox.ensure_session(context.operation_id)
        result = self.sandbox.run_python(
            session_id=context.operation_id,
            code=wrap_python(node.code),
            inputs=inputs_to_sandbox,
            operation_id=context.operation_id,
        )
        self.repository.update_diagnostics(
            context.run_id,
            context.node_id,
            context.attempt,
            {
                "code": node.code,
                "sandbox_job_id": result.session_id,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "oom_killed": result.oom_killed,
            },
        )
        if result.exit_code != 0 or result.timed_out or result.oom_killed:
            raise WorkflowError(
                "python_execution_failed", result.stderr or "Python завершился с ошибкой."
            )
        if (
            sum(item.size_bytes for item in result.outputs)
            > self.artifacts.max_session_output_bytes
        ):
            raise WorkflowError("python_output_size_limit")
        published: list[dict[str, Any]] = []
        manifest: dict[str, Any] | None = None
        for output in result.outputs:
            with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as fileobj:
                is_manifest = output.filename == OUTPUT_MANIFEST
                self.sandbox.download_output(
                    session_id=result.session_id,
                    output_id=output.output_id,
                    destination=cast(BinaryIO, fileobj),
                    max_bytes=self.data.max_json_bytes
                    if is_manifest
                    else self.artifacts.max_output_bytes,
                )
                if is_manifest:
                    manifest = json.load(fileobj)
                    continue
                artifact = self.artifacts.store_generated(
                    workspace_id=context.user.workspace_id,
                    owner_user_id=context.user.id,
                    conversation_id=context.conversation_id,
                    filename=output.filename,
                    declared_media_type=output.media_type,
                    fileobj=cast(BinaryIO, fileobj),
                    source=ArtifactSource.SANDBOX_OUTPUT,
                    source_artifact_ids=[item.artifact_id for item in inputs_to_sandbox[:-1]],
                    artifact_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"scenario-python/{context.operation_id}/{output.output_id}",
                        )
                    ),
                )
                self.repository.pin_artifact(context.run_id, artifact.id, context.node_id)
                published.append(self.artifacts.to_ref(artifact).model_dump(mode="json"))
        if not isinstance(manifest, dict) or "result" not in manifest:
            raise WorkflowError("python_result_missing")
        return {"result": manifest["result"], "files": published}
