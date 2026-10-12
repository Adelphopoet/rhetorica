"""Durable SDK coordinator turn for an admitted coding-agent team."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx2
from openai import AsyncOpenAI
from rhetorica_backend.contracts.coding import (
    AgentTask,
    CodingAgentTeamMember,
    CodingCoordinatorDecision,
    CodingRunAdmission,
)
from rhetorica_backend.domain.chat.llm import sdk_models
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.provider_capabilities import (
    ProviderRuntimeCapabilities,
)
from rhetorica_backend.domain.coding.chat_events import CodingSpecialistChatHooks, _bounded_text
from rhetorica_backend.domain.coding.errors import (
    CodingBudgetExhausted,
    CodingRunError,
    CodingToolAccessDenied,
)
from rhetorica_backend.domain.coding.model import CodingBudgetModel
from rhetorica_backend.domain.coding.policies import DEFAULT_ASSISTANT_POLICY_ID
from rhetorica_backend.domain.coding.team_coordinator import (
    build_member_tool,
    build_team_coordinator,
    coordinator_instructions,
)
from rhetorica_backend.domain.coding.teams import run_team_snapshot
from rhetorica_backend.infra.postgres.agent_session import PostgresAgentSessionStore

from agents import RunConfig, Runner, ToolExecutionConfig
from agents.exceptions import AgentsException, MaxTurnsExceeded

if TYPE_CHECKING:
    from rhetorica_backend.domain.coding.deadlines import CodingOperationDeadline
    from rhetorica_backend.domain.coding.lifecycle import MemberInvoker
    from rhetorica_backend.domain.coding.service import CodingRunService
    from rhetorica_backend.domain.coding.specialist_runtime import CodingSpecialistRuntime
    from rhetorica_backend.infra.postgres.session import DatabaseRuntime


class CodingTeamCoordinatorRuntime:
    """Run the coordinator with only snapshot members as tools.

    Recovery: model responses are journaled by request fingerprint and the
    coordinator session is reset to the start of the operation, so a retried
    activity replays the same tool calls. Each member call is journaled by its
    tool call ID and returns its first recorded result instead of running again.
    """

    def __init__(
        self,
        *,
        service: CodingRunService,
        specialists: CodingSpecialistRuntime,
        db: DatabaseRuntime,
    ) -> None:
        self.service = service
        self.specialists = specialists
        self.db = db

    async def run(
        self,
        admission: CodingRunAdmission,
        task: AgentTask,
        operation_id: str,
        deadline: CodingOperationDeadline,
        *,
        invoke_member: MemberInvoker,
    ) -> CodingCoordinatorDecision:
        r = admission.run
        result_scope = {
            "run_id": r.id,
            "task_id": task.id,
            "operation_id": f"coordinator-result:{operation_id}",
        }
        journal = self.service.journal
        if journal:
            completed = await asyncio.to_thread(journal.get, **result_scope)
            if completed is not None:
                return CodingCoordinatorDecision.model_validate(completed["decision"])
        user = self._authorize(admission)
        team = run_team_snapshot(r)
        member_tasks = self._member_tasks(admission)
        profile = (
            None
            if task.agent_profile_id == DEFAULT_ASSISTANT_POLICY_ID
            else self.service.agent(user, task.agent_profile_id)
        )
        connection = self.specialists.connection(r.workspace_id, profile)
        structured = LlmClient._structured_output_enabled(connection)
        question = await self._question(admission, task)

        async def call(
            member: CodingAgentTeamMember, call_id: str, assignment: str
        ) -> dict[str, Any]:
            return await self._call_member(
                admission, member, member_tasks, call_id, assignment, invoke_member
            )

        def authorize_finish() -> None:
            self._authorize(admission)

        member_tools = [build_member_tool(member=m, call=call) for m in team.members]
        capabilities = ProviderRuntimeCapabilities.from_settings(connection.extra_settings)
        model_settings = replace(
            sdk_models.build_model_settings(connection),
            parallel_tool_calls=False,
            truncation="disabled",
        )
        endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
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
                journal=journal,
                usage_format=capabilities.usage_format,
                model_name=connection.model,
            )
            agent = build_team_coordinator(
                name=(profile.title if profile is not None else "Coordinator"),
                instructions=coordinator_instructions(
                    team=team,
                    profile_prompt=(profile.admin_prompt or "") if profile is not None else "",
                    structured_output=structured,
                ),
                member_tools=member_tools,
                model=model,
                structured_output=structured,
                authorize_finish=authorize_finish,
            )
            agent.model_settings = model_settings
            store = PostgresAgentSessionStore(self.db, r.owner_user_id, workspace_id=r.workspace_id)
            session = await asyncio.to_thread(store.for_task, task.id)
            await asyncio.to_thread(store.begin_operation, task.id, operation_id)

            try:
                async with asyncio.timeout(deadline.timeout_seconds):
                    result = await Runner.run(
                        agent,
                        question,
                        context=admission.principal.model_copy(update={"task_id": task.id}),
                        max_turns=task.max_iterations,
                        hooks=CodingSpecialistChatHooks(runs=self.service.runs, task=task),
                        run_config=RunConfig(
                            tracing_disabled=True,
                            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                        ),
                        session=session,
                    )
            except TimeoutError as exc:
                raise CodingBudgetExhausted("coding_runtime_limit_reached") from exc
            except MaxTurnsExceeded as exc:
                raise CodingBudgetExhausted() from exc
            except AgentsException as exc:
                # Unwrap tool failures: governed stops keep their reason, transient
                # runner errors stay retryable by the activity.
                cause = exc.__cause__
                if cause is not None and not isinstance(cause, AgentsException):
                    raise cause from None
                raise
        decision = self._decision(result.final_output)
        if journal:
            await asyncio.to_thread(
                journal.put, **result_scope, value={"decision": decision.model_dump(mode="json")}
            )
        await asyncio.to_thread(
            self.service.runs.record_chat_event,
            run_id=r.id,
            task_id=task.id,
            workspace_id=r.workspace_id,
            owner_user_id=r.owner_user_id,
            event_type="team.coordinator_decision",
            event_key=operation_id,
            payload={"decision": decision.decision, "summary": _visible(decision.summary)},
        )
        return decision

    async def _call_member(
        self,
        admission: CodingRunAdmission,
        member: CodingAgentTeamMember,
        member_tasks: dict[str, AgentTask],
        call_id: str,
        assignment: str,
        invoke_member: MemberInvoker,
    ) -> dict[str, Any]:
        r = admission.run
        member_task = member_tasks.get(member.id)
        if member_task is None:
            raise CodingToolAccessDenied("coding_team_member_task_required")
        scope = {
            "run_id": r.id,
            "task_id": member_task.id,
            "operation_id": f"member-result:{call_id}",
        }
        journal = self.service.journal
        if journal:
            completed = await asyncio.to_thread(journal.get, **scope)
            if completed is not None:
                cached: dict[str, Any] = completed["result"]
                return cached
        self._authorize(admission)
        event = {
            "run_id": r.id,
            "task_id": member_task.id,
            "workspace_id": r.workspace_id,
            "owner_user_id": r.owner_user_id,
            "event_key": call_id,
        }
        await asyncio.to_thread(
            self.service.runs.record_chat_event,
            **event,
            event_type="team.member_started",
            payload={
                "member_id": member.id,
                "member_label": member.label,
                "capability": member.capability,
                "assignment": _visible(assignment),
                "assignment_truncated": _visible(assignment) != assignment,
            },
        )
        result = await invoke_member(member.id, call_id, assignment)
        result = await self._with_full_review(r.id, member_task.id, member.id, result)
        if journal:
            await asyncio.to_thread(journal.put, **scope, value={"result": result})
        await asyncio.to_thread(
            self.service.runs.record_chat_event,
            **event,
            event_type="team.member_completed",
            payload={
                "member_id": member.id,
                "member_label": member.label,
                "status": result.get("status"),
                "candidate_digest": result.get("candidate_digest"),
                "summary": _visible(str(result.get("summary") or result.get("reason") or "")),
            },
        )
        return result

    async def _with_full_review(
        self, run_id: str, task_id: str, member_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep a reviewer's full report per candidate so a repeat call costs no tokens."""

        journal = self.service.journal
        digest = result.get("candidate_digest")
        if journal is None or not digest or result.get("status") == "candidate_recorded":
            return result
        scope = {
            "run_id": run_id,
            "task_id": task_id,
            "operation_id": f"review-report:{member_id}:{digest}",
        }
        if result.get("repeat"):
            stored = await asyncio.to_thread(journal.get, **scope)
            if stored is not None:
                return {**result, "reason": stored["reason"]}
            return result
        await asyncio.to_thread(journal.put, **scope, value={"reason": result.get("reason", "")})
        return result

    def _authorize(self, admission: CodingRunAdmission) -> Any:
        r = admission.run
        account = self.service.users.get_by_id(user_id=r.owner_user_id)
        if account is None:
            raise CodingRunError("workspace_access_denied", status_code=403)
        user = account.for_workspace(r.workspace_id)
        self.service.require_actor(user, auth_kind="session", write=True)
        self.service.resources.validate_run(r, user)
        return user

    def _member_tasks(self, admission: CodingRunAdmission) -> dict[str, AgentTask]:
        r = admission.run
        tasks = self.service.runs.tasks(
            run_id=r.id, workspace_id=r.workspace_id, owner_user_id=r.owner_user_id
        )
        mapped = {task.team_member_id: task for task in tasks if task.team_member_id}
        if not mapped and r.team_snapshot is None:
            # Runs admitted before coding teams carry exactly one task per role.
            by_role = {task.role: task for task in tasks}
            mapped = {
                member_id: by_role[role]
                for member_id, role in [("developer", "developer"), ("qa", "qa")]
                if role in by_role
            }
        return mapped

    async def _question(self, admission: CodingRunAdmission, task: AgentTask) -> str:
        r = admission.run
        instruction = (
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

        instruction = project_instructions(instruction)
        context = {
            "project": r.repository.project_id,
            "target_branch": r.repository.target_branch,
            "base_sha": r.repository.base_sha,
            "dbt_project_subdir": r.data_warehouse.dbt_project_subdir if r.data_warehouse else None,
        }
        return instruction + "\n\nRun context: " + json.dumps(context, ensure_ascii=False)

    @staticmethod
    def _decision(output: Any) -> CodingCoordinatorDecision:
        if isinstance(output, CodingCoordinatorDecision):
            return output
        try:
            return CodingCoordinatorDecision.model_validate_json(str(output))
        except ValueError:
            return CodingCoordinatorDecision(
                decision="stop",
                summary="The coordinator finished without a valid structured decision.",
            )


def _visible(text: str) -> str:
    """Bound log text by escaped JSON bytes; the event log rejects payloads above 4 KB."""

    return _bounded_text(text, 1_500)
