"""Authoring, admission and inspection use cases for asynchronous scenarios."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from uuid import uuid4

from rhetorica_backend.contracts.scenario_workflows import (
    SaveScenarioDraftRequest,
    ScenarioAiProposal,
    ScenarioAiProposalRequest,
    ScenarioDraft,
    ScenarioRun,
    ScenarioVersion,
    ScenarioWorkflowDefinition,
    StartScenarioRunRequest,
    WorkflowValidationResult,
)
from rhetorica_backend.domain.scenarios.authoring_assistant import (
    ProgressCallback,
    ScenarioAuthoringAssistant,
)
from rhetorica_backend.domain.scenarios.data_store import ScenarioDataStore
from rhetorica_backend.domain.scenarios.errors import (
    WorkflowConflict,
    WorkflowError,
    WorkflowNotFound,
)
from rhetorica_backend.domain.scenarios.graph import isolated_step_definition, validate_workflow
from rhetorica_backend.domain.scenarios.llm_step import ScenarioLlmStepRunner
from rhetorica_backend.domain.scenarios.resources import ScenarioResourcePolicy
from rhetorica_backend.domain.scenarios.values import (
    check_schema,
    validate_parameters,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.scenario_authoring_repository import (
    ScenarioAuthoringRepository,
)
from rhetorica_backend.infra.postgres.scenario_run_repository import ScenarioRunRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountItem


def artifact_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        if isinstance(value.get("id"), str) and "filename" in value:
            return {value["id"]}
        return set().union(*(artifact_ids(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(artifact_ids(item) for item in value))
    return set()


class ScenarioWorkflowService:
    def __init__(
        self,
        authoring: ScenarioAuthoringRepository,
        runs: ScenarioRunRepository,
        resources: ScenarioResourcePolicy,
        chats: ChatRepository,
        data: ScenarioDataStore,
        llm: ScenarioLlmStepRunner,
        *,
        enabled: bool,
    ):
        self.authoring = authoring
        self.runs = runs
        self.resources = resources
        self.chats = chats
        self.data = data
        self.llm = llm
        self.enabled = enabled

    def require_enabled(self) -> None:
        if not self.enabled:
            raise WorkflowError(
                "scenario_workflows_disabled", "Новые workflow-запуски отключены.", status_code=503
            )

    def validate(
        self, graph: ScenarioWorkflowDefinition, user: UserAccountItem
    ) -> WorkflowValidationResult:
        validation = validate_workflow(graph)
        if validation.valid:
            try:
                self.resources.snapshots(graph, user)
            except WorkflowError as exc:
                from rhetorica_backend.contracts.scenario_workflows import WorkflowValidationIssue

                validation.issues.append(WorkflowValidationIssue(code=exc.code, message=str(exc)))
                validation.valid = False
        return validation

    def save(
        self, scenario_id: str, user: UserAccountItem, request: SaveScenarioDraftRequest
    ) -> ScenarioDraft:
        self.require_enabled()
        self._bound_json(request.model_dump(mode="json"))
        for case in request.test_cases:
            if case.expected_schema is not None:
                check_schema(case.expected_schema)
        return self.authoring.save_draft(scenario_id, user, request)

    def publish(self, scenario_id: str, user: UserAccountItem, revision: int) -> ScenarioVersion:
        self.require_enabled()
        draft = self.authoring.get_draft(scenario_id, user)
        if draft.revision != revision:
            raise WorkflowConflict()
        validation = self.validate(draft.workflow, user)
        if not validation.valid:
            raise WorkflowError(
                "invalid_workflow", "; ".join(issue.message for issue in validation.issues)
            )
        return self.authoring.publish(scenario_id, user, revision)

    def start(
        self, scenario_id: str, user: UserAccountItem, request: StartScenarioRunRequest, key: str
    ) -> ScenarioRun:
        if not key or len(key) > 128:
            raise WorkflowError("idempotency_key_required")
        body = {"scenario_id": scenario_id, **request.model_dump(mode="json")}
        self._bound_json(body)
        fingerprint = hashlib.sha256(
            json.dumps(body, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        duplicate = self.runs.find_request(user, key, fingerprint)
        if duplicate:
            return duplicate
        self.require_enabled()
        if request.version is not None and request.draft_revision is not None:
            raise WorkflowError("choose_version_or_draft")
        if request.test_node_id is not None and (
            request.draft_revision is None or request.test_inputs is None
        ):
            raise WorkflowError("step_test_requires_draft_and_explicit_inputs")
        version, kind, definition, graph, title = self.runs.resolve_definition(
            scenario_id, user, request
        )
        execution_graph = (
            isolated_step_definition(graph, request.test_node_id, request.test_inputs or {})
            if request.test_node_id
            else graph
        )
        if kind != "legacy":
            validation = self.validate(execution_graph, user)
            if not validation.valid:
                raise WorkflowError(
                    "invalid_workflow", "; ".join(issue.message for issue in validation.issues)
                )
        inputs = (
            {} if request.test_node_id else validate_parameters(request.inputs, graph.parameters)
        )
        if request.test_node_id:
            node = next(node for node in execution_graph.nodes if node.id == request.test_node_id)
            test_inputs = (
                validate_parameters(request.test_inputs or {}, graph.parameters)
                if node.type == "input"
                else {name: binding.value for name, binding in node.inputs.items()}
            )
            request = request.model_copy(update={"test_inputs": test_inputs})
        if request.expected_schema is not None:
            check_schema(request.expected_schema)
        snapshots = self.resources.snapshots(
            execution_graph, user, legacy=definition if kind == "legacy" else None
        )
        files = (
            artifact_ids(inputs)
            | artifact_ids(request.test_inputs)
            | artifact_ids([
                binding.value
                for node in execution_graph.nodes
                for binding in node.inputs.values()
                if binding.source == "constant"
            ])
        )
        conversation_id = request.conversation_id
        if conversation_id:
            conversation = self.chats.get_conversation(conversation_id, owner_user_id=user.id)
            if conversation is None or conversation.workspace_id != user.workspace_id:
                raise WorkflowNotFound("conversation_not_found")
        elif files:
            raise WorkflowError("file_inputs_require_conversation")
        else:
            conversation_id = self.chats.create_conversation(
                user.workspace_id, title=title, owner_user_id=user.id
            ).id
        return self.runs.create(
            run_id=str(uuid4()),
            scenario_id=scenario_id,
            title=title,
            user=user,
            request=request,
            key=key,
            request_hash=fingerprint,
            version=version,
            kind=kind,
            definition=definition,
            workflow=graph,
            agent_snapshots=snapshots,
            conversation_id=conversation_id,
            inputs=inputs,
            artifact_ids=sorted(files),
        )

    def get(self, run_id: str, user: UserAccountItem) -> ScenarioRun:
        run, ref = self.runs.get(run_id, user)
        if ref:
            run.result = self.data.get(ref, run_id=run_id)
        for step in run.steps:
            if step.input_ref:
                step.inputs = self.data.get(step.input_ref, run_id=run_id)
            if step.output_ref:
                step.outputs = self.data.get(step.output_ref, run_id=run_id)
        return run

    async def propose(
        self,
        scenario_id: str,
        user: UserAccountItem,
        request: ScenarioAiProposalRequest,
        progress: ProgressCallback | None = None,
    ) -> ScenarioAiProposal:
        self.require_enabled()
        draft = await asyncio.to_thread(self.authoring.get_draft, scenario_id, user)
        if draft.revision != request.expected_revision:
            raise WorkflowConflict()
        return await ScenarioAuthoringAssistant(self.llm, self.resources, self.validate).propose(
            draft, user, request, progress
        )

    @staticmethod
    def _bound_json(value: Any) -> None:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 1024 * 1024:
            raise WorkflowError("scenario_json_limit")
