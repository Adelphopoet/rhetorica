"""Map typed specialist tools to a scoped runner and read-only query gateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from rhetorica_backend.contracts.coding import SandboxExecutionRequest, ServerValidationEvidence
from rhetorica_backend.domain.coding.commands import CodingCommandPolicy
from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted, CodingToolAccessDenied


class CodingRuntimeToolGateway:
    def __init__(
        self,
        *,
        runner,
        skills,
        service,
        settings,
        deadline,
        candidate=None,
        restore_mutations=False,
    ):
        self.runner = runner
        self.skills = skills
        self.service = service
        self.settings = settings
        self.deadline = deadline
        self.candidate = candidate
        self._restore_mutations = restore_mutations

    async def invoke(self, *, name, task, principal, arguments):
        # Authorization runs even when replaying a successful tool receipt.
        result = await self._invoke(name=name, task=task, principal=principal, arguments=arguments)
        if arguments._call_id and self.service.journal:
            await asyncio.to_thread(
                self.service.journal.put,
                run_id=task.run_id,
                task_id=task.id,
                operation_id=f"tool:{arguments._call_id}",
                value=result,
            )
        return result

    async def _invoke(self, *, name, task, principal, arguments):
        remaining = int((self.deadline.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise CodingBudgetExhausted("coding_runtime_limit_reached")
        run = self.service.runs.get(
            run_id=task.run_id, workspace_id=task.workspace_id, owner_user_id=task.owner_user_id
        )
        if run.status in {"cancel_requested", "cancelled"}:
            raise CodingToolAccessDenied("coding_run_cancelled")
        self.service.resources.validate_run(
            run,
            self.service.users.get_by_id(user_id=run.owner_user_id).for_workspace(run.workspace_id),
        )
        if arguments._call_id and self.service.journal:
            cached = await asyncio.to_thread(
                self.service.journal.get,
                run_id=task.run_id,
                task_id=task.id,
                operation_id=f"tool:{arguments._call_id}",
            )
            if cached is not None:
                if (
                    self._restore_mutations
                    and name == "repository_apply_patch"
                    and cached.get("exit_code") == 0
                ):
                    restored = await asyncio.to_thread(
                        self.runner.execute,
                        task.id,
                        SandboxExecutionRequest(
                            operation="apply_patch",
                            arguments={"patch": arguments.patch},
                            timeout_seconds=min(remaining, 300),
                        ),
                    )
                    if restored.exit_code != 0:
                        raise CodingToolAccessDenied("coding_sandbox_replay_failed")
                return cached
        if name == "validate_dbt":
            from rhetorica_backend.domain.coding.agents import _DbtToolRequest, _RunDbtInput
            from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer

            steps = [
                ("parse", []),
                ("list", arguments.selectors),
                ("compile", arguments.selectors),
                *(("show", [selector]) for selector in arguments.selectors),
            ]
            results = []
            for index, (command, selectors) in enumerate(steps):
                step = _RunDbtInput(
                    request=_DbtToolRequest(
                        command=command,
                        project_subdir=arguments.project_subdir,
                        selectors=selectors,
                        show_limit=arguments.show_limit if command == "show" else None,
                        no_introspect=command == "compile",
                        no_populate_cache=command == "compile",
                    )
                )
                step._call_id = (
                    f"{arguments._call_id}:{command}:{index}" if arguments._call_id else None
                )
                result = await self.invoke(
                    name="run_dbt", task=task, principal=principal, arguments=step
                )
                results.append(CodingToolOutputSerializer.project(result, call_id=step._call_id))
                receipt = result.get("dbt_receipt") or {}
                if (
                    result.get("exit_code") != 0
                    or result.get("truncated")
                    or receipt.get("status") != "passed"
                    or receipt.get("incomplete")
                ):
                    break
            completed = len(results) == len(steps) and all(
                (check.get("dbt_receipt") or {}).get("exit_code") is not None
                and not check["dbt_receipt"].get("incomplete")
                and not check.get("truncated")
                for check in results
            )
            passed = completed and all(
                check.get("exit_code") == 0 and check["dbt_receipt"]["status"] == "passed"
                for check in results
            )
            return {
                "status": "passed"
                if passed
                else "blocked"
                if receipt.get("status") == "blocked" or result.get("truncated")
                else "failed",
                "checks": results,
                "completed": completed,
                "materialization_status": "not_run",
            }
        if name == "read_tool_result":
            from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer

            receipt = (
                await asyncio.to_thread(
                    self.service.journal.get,
                    run_id=task.run_id,
                    task_id=task.id,
                    operation_id=f"tool:{arguments.call_id}",
                )
                if self.service.journal
                else None
            )
            if receipt is None:
                return {"status": "failed", "reason_code": "coding_tool_result_not_found"}
            value = receipt if arguments.field == "all" else receipt.get(arguments.field)
            if value is None:
                return {"status": "failed", "reason_code": "coding_tool_result_field_missing"}
            raw = value if isinstance(value, str) else CodingToolOutputSerializer.encode(value)
            content = raw[arguments.offset : arguments.offset + arguments.max_chars]
            next_offset = arguments.offset + len(content)
            return {
                "content": content,
                "offset": arguments.offset,
                "next_offset": next_offset,
                "total_chars": len(raw),
                "truncated": next_offset < len(raw),
            }
        if name == "candidate_diff":
            if (
                self.candidate is None
                or run.candidate is None
                or run.candidate.digest != self.candidate.digest
            ):
                raise CodingToolAccessDenied("coding_candidate_diff_mismatch")
            if arguments.path is not None and arguments.path not in self.candidate.changed_paths:
                return {
                    "status": "invalid_arguments",
                    "reason_code": "coding_candidate_diff_path_not_changed",
                    "changed_paths": self.candidate.changed_paths,
                    "instruction": "Use path=null for the complete diff, or one exact changed path.",
                }
            user = self.service.users.get_by_id(user_id=run.owner_user_id).for_workspace(
                run.workspace_id
            )
            return await asyncio.to_thread(
                self.service.diff,
                run.id,
                user,
                path=arguments.path,
                offset=arguments.offset,
                max_chars=arguments.max_chars,
            )
        values = arguments.model_dump(exclude_none=True)
        if name == "workspace_skill_read":
            fragment = await asyncio.to_thread(self.skills.read, principal=principal, **values)
            return fragment.model_dump(mode="json")
        if name == "task_status":
            return {"run_id": run.id, "status": run.status, "phase": run.phase}
        operations = {
            "repository_read": "read_file",
            "repository_search": "search",
            "repository_apply_patch": "apply_patch",
            "repository_run_command": "run_command",
        }
        if name in operations:
            if name == "repository_search":
                values = {
                    "pattern": values["query"],
                    "path_prefix": values.get("path_prefix") or ".",
                    "limit": values["limit"],
                }
            if name == "repository_apply_patch":
                values = {"patch": values["patch"]}
            if name == "repository_run_command":
                argv = values["argv"]
                try:
                    CodingCommandPolicy().validate(argv)
                except CodingToolAccessDenied as exc:
                    # A denied command has no side effect; the model can choose
                    # an allowed tool. Identity/resource denials above remain fatal.
                    return {"status": "denied", "reason_code": exc.code}
            request = SandboxExecutionRequest(
                operation=operations[name], arguments=values, timeout_seconds=min(remaining, 300)
            )
            result = await asyncio.to_thread(self.runner.execute, task.id, request)
        elif name == "run_dbt":
            from pydantic import ValidationError

            try:
                values["request"] = arguments.request.to_request().model_dump(mode="json")
            except ValidationError as exc:
                return {
                    "status": "invalid_arguments",
                    "errors": exc.errors(
                        include_input=False, include_context=False, include_url=False
                    ),
                }
            if run.data_warehouse is None:
                raise CodingToolAccessDenied("coding_database_connector_required")
            async with httpx.AsyncClient(timeout=min(remaining, 30)) as client:
                response = await client.post(
                    f"{self.settings.coding_query_gateway_url}/internal/v1/dbt-profiles",
                    headers={
                        "X-Rhetorica-Coding-Token": self.settings.coding_query_gateway_internal_token
                    },
                    json={
                        "principal": principal.model_dump(mode="json"),
                        "connector_id": run.data_warehouse.connector_id,
                        "expires_at": self.deadline.expires_at.isoformat(),
                    },
                )
                response.raise_for_status()
                profile = response.json()["runtime_profile"]
            request = SandboxExecutionRequest(
                operation="run_dbt",
                timeout_seconds=min(remaining, 300),
                dbt_request=values["request"],
                dbt_runtime_profile=profile,
            )
            result = await asyncio.to_thread(self.runner.execute, task.id, request)
        elif name == "run_query":
            # Query results are returned to this task only and never attached to publication.
            if run.data_warehouse is None:
                raise CodingToolAccessDenied("coding_database_connector_required")
            async with httpx.AsyncClient(timeout=min(remaining, 130)) as client:
                response = await client.post(
                    f"{self.settings.coding_query_gateway_url}/internal/v1/query-capabilities",
                    headers={
                        "X-Rhetorica-Coding-Token": self.settings.coding_query_gateway_internal_token
                    },
                    json={
                        "principal": principal.model_dump(mode="json"),
                        "connector_id": run.data_warehouse.connector_id,
                        "expires_at": self.deadline.expires_at.isoformat(),
                    },
                )
                response.raise_for_status()
                issued = response.json()
                response = await client.post(
                    f"{self.settings.coding_query_gateway_url}/v1/query",
                    headers={"X-Rhetorica-Query-Capability": issued["capability"]},
                    json=values["request"],
                )
                response.raise_for_status()
                return response.json()
        else:
            raise CodingToolAccessDenied("coding_tool_not_supported")
        if self.candidate and (
            name == "run_dbt"
            or (
                name == "repository_run_command"
                and CodingCommandPolicy.is_validation(values["argv"])
            )
        ):
            status = (
                "blocked"
                if result.truncated or result.exit_code in {124, 127}
                else "passed"
                if result.exit_code == 0
                else "failed"
            )
            await asyncio.to_thread(
                self.service.runs.record_validation_evidence,
                ServerValidationEvidence(
                    id=str(uuid4()),
                    workspace_id=run.workspace_id,
                    owner_user_id=run.owner_user_id,
                    run_id=run.id,
                    task_id=task.id,
                    candidate_digest=self.candidate.digest,
                    name=f"{name}:{request.dbt_request.command if request.dbt_request else ' '.join(values['argv'])[:90]}",
                    source="dbt" if name == "run_dbt" else "sandbox",
                    status=status,
                    mandatory=not (
                        result.dbt_receipt and result.dbt_receipt.evidence_scope == "baseline"
                    ),
                    reason="Production relation baseline; candidate materialization was not validated"
                    if result.dbt_receipt and result.dbt_receipt.evidence_scope == "baseline"
                    else None,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    created_at=datetime.now(UTC),
                ),
            )
        return result.model_dump(mode="json")
