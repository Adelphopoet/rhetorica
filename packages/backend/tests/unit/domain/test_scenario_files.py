from __future__ import annotations

import asyncio
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from rhetorica_backend.contracts.execution import EngineConnectionConfig, QueryPreview
from rhetorica_backend.contracts.scenario_workflows import (
    InputBinding,
    InputNode,
    PythonNode,
    ResultNode,
    ScenarioEdge,
    ScenarioWorkflowDefinition,
    SqlNode,
    StartScenarioRunRequest,
    ValueSpec,
)
from rhetorica_backend.contracts.scenarios import ScenarioConnectorBindings, ScenarioToolPolicy
from rhetorica_backend.domain.files.models import SandboxOutput, SandboxRunResult, StoredObject
from rhetorica_backend.domain.files.policy import ArtifactPolicy
from rhetorica_backend.domain.files.service import ArtifactService
from rhetorica_backend.domain.files.sql_export import SqlResultExportService
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import WorkflowError
from rhetorica_backend.domain.scenarios.python_step import ScenarioPythonStepRunner
from rhetorica_backend.domain.scenarios.sql_step import ScenarioSqlStepRunner, bind_sql
from rhetorica_backend.domain.scenarios.step_context import ScenarioStepContext
from rhetorica_backend.domain.scenarios.values import resolve_bindings
from rhetorica_backend.infra.object_storage import ObjectStorageSqlResultSnapshotStore
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from rhetorica_backend.infra.postgres.scenario_repository import ScenarioRepository
from rhetorica_backend.infra.postgres.scenario_run_repository import ScenarioRunRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def upload_fileobj(self, *, key, fileobj, size_bytes, media_type):
        body = fileobj.read()
        assert len(body) == size_bytes
        self.objects[key] = (body, media_type)

    def open(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        body, media_type = self.objects[key]
        return StoredObject(io.BytesIO(body), len(body), media_type)

    def create_download_url(self, key, **_kwargs):
        return key

    def delete(self, key):
        self.objects.pop(key, None)


def test_sql_parameters_are_literals_and_never_sql_fragments() -> None:
    rendered = bind_sql(
        "SELECT :value AS value", {"value": "'; DROP TABLE secrets; --"}, "postgres"
    )
    assert rendered == "SELECT '''; DROP TABLE secrets; --' AS value"
    with pytest.raises(WorkflowError, match="scalar"):
        bind_sql("SELECT :value", {"value": ["a"]}, "postgres")
    with pytest.raises(WorkflowError, match="single_sql"):
        bind_sql("SELECT 1; DELETE FROM test", {}, "postgres")


@pytest.mark.parametrize("interruption", [None, "publication", "close"])
def test_full_sql_file_reaches_python_and_remains_owned_after_cleanup(
    tmp_path: Path, monkeypatch, interruption: str | None
) -> None:
    db = DatabaseRuntime(f"sqlite:///{tmp_path}/pipeline.db")
    db.create_schema()
    user = UserAccountRepository(db).ensure_bootstrap_user("default")
    chats = ChatRepository(db)
    conversation = chats.create_conversation("default", owner_user_id=user.id)
    storage = MemoryStorage()
    artifacts = ArtifactService(
        repository=FileArtifactRepository(db), storage=storage, policy=ArtifactPolicy()
    )
    upload = artifacts.stage_upload(
        workspace_id="default",
        owner_user_id=user.id,
        conversation_id=conversation.id,
        filename="selected-stores.csv",
        declared_media_type="text/csv",
        fileobj=io.BytesIO(b"whs_id\n123\n"),
    )
    upload_ref = artifacts.to_ref(upload).model_dump(mode="json")
    scenario = ScenarioRepository(db).create_definition(
        slug="files",
        workspace_id="default",
        owner_user_id=user.id,
        scope="personal",
        title="Files",
        summary="Files",
        description="",
        input_schema={},
        prompt_template="Files",
        tool_policy=ScenarioToolPolicy(),
        default_connector_bindings=ScenarioConnectorBindings(),
        status="published",
    )
    graph = ScenarioWorkflowDefinition(
        parameters={"upload": ValueSpec(type="file", title="Selected stores")},
        nodes=[
            InputNode(id="start", title="Start"),
            SqlNode(id="sql", title="SQL", connector_name="db", sql="SELECT 1"),
            PythonNode(
                id="python",
                title="Python",
                code="result = {'processed': True}",
                inputs={
                    "file": InputBinding(source="step", node_id="sql", path=["file"], type="file"),
                    "upload": InputBinding(source="input", path=["upload"], type="file"),
                },
            ),
            ResultNode(id="end", title="Result"),
        ],
        edges=[
            ScenarioEdge(source=a, target=b)
            for a, b in [("start", "sql"), ("sql", "python"), ("python", "end")]
        ],
    )
    run_id = str(uuid4())
    ScenarioRunRepository(db).create(
        run_id=run_id,
        scenario_id=scenario.id,
        title="Files",
        user=user,
        request=StartScenarioRunRequest(),
        key="files",
        request_hash="files",
        version=1,
        kind="workflow",
        definition=graph.model_dump(mode="json"),
        workflow=graph,
        agent_snapshots={},
        conversation_id=conversation.id,
        inputs={"upload": upload_ref},
        artifact_ids=[upload.id],
    )
    repository = ScenarioActivityRepository(db)
    data = ScenarioDataStore(storage)
    snapshot_store = ObjectStorageSqlResultSnapshotStore(storage=storage)
    sql_context = ScenarioStepContext(
        run_id, "sql", repository.load_step(run_id, "sql").operation_id, 1, user, conversation.id
    )
    capture = snapshot_store.begin(
        execution_id=sql_context.operation_id,
        workspace_id="default",
        owner_user_id=user.id,
        conversation_id=conversation.id,
    )
    capture.start(["number", "label"])
    capture.write_rows([[index, "=original-text"] for index in range(1200)])
    snapshot = capture.finish(possibly_truncated=False)
    history = SimpleNamespace(
        status="succeeded",
        workspace_id="default",
        actor_id=user.id,
        normalized_sql="SELECT 1",
        duration_ms=1,
        preview=QueryPreview(columns=["number", "label"], rows=[[0, "preview-only"]]),
        warnings=[],
    )

    class CompletedSql:
        def get(self, _execution_id):
            return history

        def get_snapshot(self, _execution_id):
            return snapshot

        def execute_prepared(self, *_args):
            raise AssertionError("Completed SQL executed twice")

    exports = SqlResultExportService(
        execution_reader=CompletedSql(),
        message_reader=chats,
        artifact_service=artifacts,
        snapshot_store=snapshot_store,
    )
    resources = SimpleNamespace(
        database=lambda *_args, **_kwargs: EngineConnectionConfig(
            engine="greenplum", host="db", port=5432, username="test", database="test"
        )
    )
    sql = ScenarioSqlStepRunner(CompletedSql(), exports, resources, repository)
    repository.begin_step(run_id, "sql", 1, "inputs")
    sql_output = asyncio.run(sql.run(graph.nodes[1], {}, sql_context))
    # Worker restart after artifact persistence reuses the same execution and file identity.
    assert (
        asyncio.run(sql.run(graph.nodes[1], {}, sql_context))["file"]["id"]
        == sql_output["file"]["id"]
    )
    repository.commit_result(run_id, "sql", 1, data, sql_output, 1)
    python_inputs = resolve_bindings(
        graph.nodes[2].inputs, {"upload": upload_ref}, {"sql": sql_output}
    )

    class Sandbox:
        def __init__(self):
            self.cached = None
            self.calls = 0
            self.output_bytes = {}
            self.sessions = set()
            self.fail_close = interruption == "close"

        def ensure_session(self, session_id):
            assert not self.sessions or session_id in self.sessions, "sandbox capacity exhausted"
            self.sessions.add(session_id)
            return session_id

        def close_session(self, session_id):
            if self.fail_close:
                self.fail_close = False
                raise RuntimeError("close interrupted")
            self.sessions.discard(session_id)
            self.cached = None
            self.output_bytes = {}

        def run_python(self, *, session_id, code, inputs, operation_id):
            if self.cached:
                return self.cached
            self.calls += 1
            files = list(inputs)
            manifest = json.load(storage.open(files[-1].download_url).body)
            assert manifest["file"] == f"/workspace/input/{files[0].filename}"
            assert manifest["upload"] == f"/workspace/input/{files[1].filename}"
            assert "inputs = _rh_json.load" in code
            content = storage.open(files[0].download_url).body
            with zipfile.ZipFile(content) as archive:
                worksheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314 - generated test XLSX
                rows = worksheet.findall(".//{*}row")
                assert len(rows) == 1201  # full stored table, not the one-row preview
                assert b"=original-text" in archive.read("xl/worksheets/sheet1.xml")
            assert storage.open(files[1].download_url).body.read() == b"whs_id\n123\n"
            self.output_bytes = {
                "data": b"count\n1200\n",
                "result": json.dumps({"result": {"rows": 1200}}).encode(),
            }
            self.cached = SandboxRunResult(
                session_id,
                0,
                "Processed 1200 rows",
                "",
                outputs=(
                    SandboxOutput(
                        "data", "processed.csv", "text/csv", len(self.output_bytes["data"]), ""
                    ),
                    SandboxOutput(
                        "result",
                        "rhetorica-result.json",
                        "application/json",
                        len(self.output_bytes["result"]),
                        "",
                    ),
                ),
            )
            return self.cached

        def download_output(self, *, output_id, destination, **_kwargs):
            destination.write(self.output_bytes[output_id])
            destination.seek(0)

    sandbox = Sandbox()
    python = ScenarioPythonStepRunner(sandbox, artifacts, data, repository)
    python_context = ScenarioStepContext(
        run_id,
        "python",
        repository.load_step(run_id, "python").operation_id,
        1,
        user,
        conversation.id,
    )
    repository.begin_step(run_id, "python", 1, "inputs")
    if interruption == "publication":
        original = storage.upload_fileobj

        def interrupt_publication(**kwargs):
            if f"/0/python-{python_context.operation_id}.json" in kwargs["key"]:
                monkeypatch.setattr(storage, "upload_fileobj", original)
                raise RuntimeError("publication interrupted")
            return original(**kwargs)

        monkeypatch.setattr(storage, "upload_fileobj", interrupt_publication)
    if interruption:
        with pytest.raises(RuntimeError, match=f"{interruption} interrupted"):
            asyncio.run(python.run(graph.nodes[2], python_inputs, python_context))
        assert sandbox.sessions == {python_context.operation_id}
        assert sandbox.calls == 1
        # The next activity attempt must recover before closing its runner.
        python_context = replace(python_context, attempt=2)
        repository.begin_step(run_id, "python", 2, "inputs")
    output = asyncio.run(python.run(graph.nodes[2], python_inputs, python_context))
    assert output["result"] == {"rows": 1200}
    assert len(output["files"]) == 1
    assert sandbox.calls == 1
    assert not sandbox.sessions
    assert asyncio.run(python.run(graph.nodes[2], python_inputs, python_context)) == output
    assert sandbox.calls == 1
    assert (
        FileArtifactRepository(db).claim_cleanup(
            staged_before=datetime.now(UTC) + timedelta(days=2)
        )
        == []
    )
    published = artifacts.get_owned(
        artifact_id=output["files"][0]["id"],
        owner_user_id=user.id,
        workspace_id=user.workspace_id,
    )
    assert set(published.source_artifact_ids) == {sql_output["file"]["id"], upload.id}
    assert storage.open(published.storage_key).body.read() == b"count\n1200\n"
    if interruption is None:
        # Separate operations within one step (as for LLM tools) release capacity too.
        for _ in range(11):
            child = replace(python_context, operation_id=str(uuid4()))
            assert asyncio.run(python.run(graph.nodes[2], python_inputs, child))["result"] == {
                "rows": 1200
            }
            assert not sandbox.sessions
        assert sandbox.calls == 12
