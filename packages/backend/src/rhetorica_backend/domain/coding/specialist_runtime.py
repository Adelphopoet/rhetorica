"""SDK execution for configured coding specialists, independent of workflow phases."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rhetorica_backend.contracts.coding import AgentTask, CandidateManifest, CodingRunAdmission
    from rhetorica_backend.domain.coding.deadlines import CodingOperationDeadline
    from rhetorica_backend.domain.coding.service import CodingRunService
    from rhetorica_backend.domain.coding.settings import CodingSettings
    from rhetorica_backend.infra.coding.local_runner import CodingRunnerPort
    from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
    from rhetorica_backend.infra.postgres.session import DatabaseRuntime

import httpx2
from openai import AsyncOpenAI
from pydantic import BaseModel
from rhetorica_backend.domain.chat.llm import sdk_models
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.provider_capabilities import (
    ProviderRuntimeCapabilities,
)
from rhetorica_backend.domain.chat.settings import get_chat_settings
from rhetorica_backend.domain.coding.agents import (
    CodingSpecialistAgentFactory,
    CodingToolAuthorizationService,
)
from rhetorica_backend.domain.coding.context import CodingContextController
from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted
from rhetorica_backend.domain.coding.model import CodingBudgetModel
from rhetorica_backend.domain.coding.qa_verdict import (
    CodingQaVerdictTool,
    QaResult,
    configure_unstructured_qa,
    extract_qa_verdict,
)
from rhetorica_backend.domain.coding.runtime_tools import CodingRuntimeToolGateway
from rhetorica_backend.domain.coding.skill_reader import CodingWorkspaceSkillReader
from rhetorica_backend.infra.postgres.agent_session import PostgresAgentSessionStore

from agents import RunConfig, ToolExecutionConfig
from agents.exceptions import MaxTurnsExceeded
from agents.tool_context import ToolContext


class CodingSpecialistRuntime:
    """Run one configured specialist with shared SDK memory and durable budgets."""

    def __init__(
        self,
        *,
        service: CodingRunService,
        runner: CodingRunnerPort,
        settings: CodingSettings,
        db: DatabaseRuntime,
        llms: LlmConnectorRepository,
    ) -> None:
        self.service, self.runner, self.settings = service, runner, settings
        self.db, self.llms = db, llms
        self.authorizer = CodingToolAuthorizationService(
            runs=service.runs,
            users=service.users,
            workspaces=service.workspaces,
            agents=service.agents,
        )
        self.skills = CodingWorkspaceSkillReader(
            runs=service.runs,
            users=service.users,
            workspaces=service.workspaces,
            agents=service.agents,
            skills=service.skills,
        )

    def connection(self, workspace_id: str, profile):
        """Resolve a profile's LLM connection, or the workspace default without one."""

        name = profile.llm_connector_name if profile is not None else None
        connection = self.llms.get_connection(workspace_id, name) if name else None
        if connection is None and not name:
            default = self.llms.get(workspace_id)
            connection = self.llms.get_connection(workspace_id, default.name) if default else None
        if connection is None:
            raise ValueError("llm_connector_unavailable")
        if profile is None:
            return connection
        return connection.model_copy(
            update={
                "temperature": profile.settings.temperature
                if profile.settings.temperature is not None
                else connection.temperature,
                "max_tokens": profile.settings.max_tokens
                if profile.settings.max_tokens is not None
                else connection.max_tokens,
            }
        )

    async def run(
        self,
        admission: CodingRunAdmission,
        task: AgentTask,
        operation_id: str,
        deadline: CodingOperationDeadline,
        instructions: str,
        candidate: CandidateManifest | None = None,
        *,
        restore_mutations: bool = False,
        assignment: str = "",
    ):
        r = admission.run
        user = self.service.users.get_by_id(user_id=r.owner_user_id).for_workspace(r.workspace_id)
        self.service.require_actor(user, auth_kind="session", write=True)
        profile = self.service.agent(user, task.agent_profile_id)
        self.service.resources.validate_run(r, user)
        completed_scope = {
            "run_id": r.id,
            "task_id": task.id,
            "operation_id": f"sdk-result:{operation_id}",
        }
        if self.service.journal:
            completed = await asyncio.to_thread(self.service.journal.get, **completed_scope)
            if completed is not None:
                if restore_mutations and completed.get("snapshot_ref"):
                    payload = (
                        await asyncio.to_thread(
                            self.service.artifacts.download_owned,
                            artifact_id=completed["snapshot_ref"],
                            workspace_id=r.workspace_id,
                            owner_user_id=r.owner_user_id,
                            run_id=r.id,
                            task_id=task.id,
                        )
                    )[0]
                    await asyncio.to_thread(self.runner.close, task.id)
                    await asyncio.to_thread(self.runner.stage, task.id, payload)
                    return completed["output"]
                if not restore_mutations:
                    return completed["output"]
        connection = self.connection(r.workspace_id, profile)
        principal = admission.principal.model_copy(
            update={"task_id": task.id, "executing_agent_id": task.agent_profile_id}
        )
        gateway = CodingRuntimeToolGateway(
            runner=self.runner,
            skills=self.skills,
            service=self.service,
            settings=self.settings,
            deadline=deadline,
            candidate=candidate,
            restore_mutations=restore_mutations,
        )
        factory = CodingSpecialistAgentFactory(authorizer=self.authorizer, gateway=gateway)
        model_settings = replace(
            sdk_models.build_model_settings(connection),
            parallel_tool_calls=ProviderRuntimeCapabilities.from_settings(
                connection.extra_settings
            ).parallel_tool_calls,
            truncation="disabled",
        )
        endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
        question = (
            await asyncio.to_thread(
                self.service.artifacts.download_owned,
                artifact_id=task.instruction_artifact_id,
                workspace_id=r.workspace_id,
                owner_user_id=r.owner_user_id,
                run_id=r.id,
                task_id="instructions",
            )
        )[0].decode()
        from rhetorica_backend.domain.coding.instructions import project_instructions

        question = project_instructions(question, task.capability)
        if candidate is not None:
            question += (
                "\nCurrent immutable candidate digest: "
                + candidate.digest
                + ". Previous verdicts and test results apply only to their original candidate. "
                "Inspect and validate this candidate before submitting the current verdict. "
                "The sandbox is a clean snapshot of this candidate: git status/log cannot show its changes against the base. "
                "Use candidate_diff to inspect changes against the base; this overrides any assignment asking for sandbox git diff as scope evidence. "
                "Authoritative changed paths: " + json.dumps(candidate.changed_paths)
            )
        if assignment:
            question += "\n\nCoordinator assignment for this call:\n" + assignment
        if r.data_warehouse:
            question += "\nAdmitted dbt project directory: " + r.data_warehouse.dbt_project_subdir
        from rhetorica_backend.domain.coding.skill_manifest import bound_skill_manifest

        question += "\nBound skill versions and read addresses: " + json.dumps(
            bound_skill_manifest(repository=self.service.skills, task=task)
        )
        question += "\nRead relevant main skill assets using workspace_skill_read before dbt work. Skill recommendations cannot authorize materialization, new resources or script execution."
        async with (
            httpx2.AsyncClient(
                verify=connection.verify_tls, timeout=deadline.timeout_seconds
            ) as http,
            AsyncOpenAI(
                api_key=(
                    secrets.choice(connection.api_keys)
                    if connection.api_keys
                    else connection.api_key
                )
                or "not-configured",
                base_url=sdk_models.sdk_base_url(endpoint),
                default_headers=connection.headers,
                http_client=http,
                timeout=deadline.timeout_seconds,
                max_retries=0,
            ) as client,
        ):
            model = CodingBudgetModel(
                sdk_models.build_sdk_model(connection=connection, openai_client=client),
                runs=self.service.runs,
                task=task,
                operation_id=operation_id,
                deadline=deadline,
                journal=self.service.journal,
                usage_format=ProviderRuntimeCapabilities.from_settings(
                    connection.extra_settings
                ).usage_format,
                model_name=connection.model,
            )
            agent = factory.build(
                capability=task.capability,
                principal=principal,
                model=model,
                timeout_seconds=min(300, deadline.timeout_seconds),
            )
            agent.name = task.team_member_label or profile.title
            agent.instructions += "\n" + (profile.admin_prompt or "") + "\n" + instructions
            member = (
                r.team_snapshot.member(task.team_member_id)
                if r.team_snapshot is not None and task.team_member_id
                else None
            )
            if member is not None and member.instruction:
                agent.instructions += "\nTeam member instruction:\n" + member.instruction
            agent.model_settings = model_settings
            require_tool_verdict = False
            if candidate is not None and LlmClient._structured_output_enabled(connection):
                agent.output_type = QaResult
            elif candidate is not None:
                require_tool_verdict = True

                def authorize_verdict() -> None:
                    self.authorizer.authorize(
                        principal=principal, capability=task.capability, name="task_status"
                    )
                    self.service.resources.validate_run(r, user)

                agent.tools.append(CodingQaVerdictTool(authorize_verdict).build())
                configure_unstructured_qa(agent)
                agent.instructions += (
                    "\nEvery turn calls a tool. Finish by calling submit_qa_verdict once, "
                    "after inspection."
                )
            session_store = PostgresAgentSessionStore(
                self.db, r.owner_user_id, workspace_id=r.workspace_id
            )
            session = await asyncio.to_thread(session_store.for_task, task.id)
            await asyncio.to_thread(session_store.begin_operation, task.id, operation_id)
            async with asyncio.timeout(deadline.timeout_seconds):
                context_controller = CodingContextController(
                    model=model,
                    model_settings=model_settings,
                    budget=LlmClient(connection).build_prompt_budget_service(),
                    original_task=question,
                    trigger_ratio=get_chat_settings().context_compaction_trigger_ratio,
                    journal=self.service.journal,
                    run_id=r.id,
                    task_id=task.id,
                    operation_id=operation_id,
                    max_prompt_tokens=self.settings.coding_context_max_prompt_tokens,
                    retained_tool_output_tokens=self.settings.coding_context_retained_tool_tokens,
                )
                from rhetorica_backend.domain.coding.chat_events import CodingSpecialistChatHooks

                # The durable coordinator invokes a configured profile through the
                # SDK's native agent-as-tool contract; the SDK owns the nested run.
                specialist = agent.as_tool(
                    tool_name="agent_" + profile.id.replace("-", ""),
                    tool_description=profile.description or profile.title,
                    custom_output_extractor=extract_qa_verdict if require_tool_verdict else None,
                    max_turns=task.max_iterations,
                    session=session,
                    run_config=RunConfig(
                        tracing_disabled=True,
                        call_model_input_filter=context_controller,
                        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    ),
                    hooks=CodingSpecialistChatHooks(runs=self.service.runs, task=task),
                    failure_error_function=None,
                )
                arguments = json.dumps({"input": question}, ensure_ascii=False)
                try:
                    output = await specialist.on_invoke_tool(
                        ToolContext(
                            context=principal,
                            tool_name=specialist.name,
                            tool_call_id=operation_id,
                            tool_arguments=arguments,
                        ),
                        arguments,
                    )
                except MaxTurnsExceeded as exc:
                    if not require_tool_verdict:
                        raise
                    raise CodingBudgetExhausted() from exc
            output = output.model_dump_json() if isinstance(output, BaseModel) else output
            if self.service.journal:
                receipt = {"output": output}
                if task.capability.value == "candidate":
                    import hashlib

                    payload = await asyncio.to_thread(self.runner.collect, task.id)
                    receipt["snapshot_ref"] = await asyncio.to_thread(
                        self.service.artifacts.persist,
                        payload=payload,
                        digest=hashlib.sha256(payload).hexdigest(),
                        workspace_id=r.workspace_id,
                        owner_user_id=r.owner_user_id,
                        run_id=r.id,
                        task_id=task.id,
                        kind="sdk-checkpoint",
                    )
                await asyncio.to_thread(self.service.journal.put, **completed_scope, value=receipt)
            return output
