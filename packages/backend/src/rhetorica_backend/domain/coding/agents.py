"""Narrow Agents SDK composition for governed coding specialists."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError
from rhetorica_backend.contracts.coding import (
    CODING_OPERATION_ARGUMENT_MAX_CHARS,
    AgentTask,
    CodingDatabaseQueryRequest,
    CodingRunStatus,
    CodingTeamMemberCapability,
    DbtCommand,
    DbtRunRequest,
    ExecutionPrincipal,
)
from rhetorica_backend.domain.coding.errors import CodingToolAccessDenied
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository

from agents import Agent, FunctionTool
from agents.models.interface import Model
from agents.strict_schema import ensure_strict_json_schema

CodingToolName = Literal[
    "repository_read",
    "repository_search",
    "repository_apply_patch",
    "repository_run_command",
    "workspace_skill_read",
    "run_dbt",
    "validate_dbt",
    "run_query",
    "task_status",
    "read_tool_result",
    "candidate_diff",
]


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _call_id: str | None = PrivateAttr(default=None)


class _RepositoryReadInput(_ToolInput):
    path: str = Field(min_length=1, max_length=1024)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=16_000, ge=1, le=16_000)


class _RepositorySearchInput(_ToolInput):
    query: str = Field(min_length=1, max_length=1_000)
    path_prefix: str | None = Field(default=None, max_length=1_024)
    limit: int = Field(default=50, ge=1, le=100)


class _RepositoryPatchInput(_ToolInput):
    patch: str = Field(
        min_length=1,
        max_length=CODING_OPERATION_ARGUMENT_MAX_CHARS,
        description="Unified Git diff. Each file starts with diff --git a/path b/path, then ---/+++ and @@ hunks. New files also need new file mode 100644 and --- /dev/null. Include a trailing newline. Split large changes into separate small patches; each patch is limited to 32000 characters.",
    )


class _RepositoryCommandInput(_ToolInput):
    argv: list[str] = Field(min_length=1, max_length=32)


class _WorkspaceSkillReadInput(_ToolInput):
    skill_id: str = Field(min_length=1, max_length=36)
    version_digest: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1_024)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=24_000, ge=1, le=24_000)


class _DbtVariable(_ToolInput):
    name: str = Field(min_length=1, max_length=128)
    value: str | int | float | bool


class _DbtToolRequest(_ToolInput):
    command: DbtCommand
    project_subdir: str = "."
    selectors: list[str] = Field(default_factory=list, max_length=50)
    excludes: list[str] = Field(default_factory=list, max_length=50)
    variables: list[_DbtVariable] = Field(default_factory=list, max_length=100)
    show_limit: int | None = Field(default=None, ge=1, le=5000)
    threads: int = Field(default=1, ge=1, le=8)
    no_introspect: bool = Field(
        default=False, description="Only compile: skip introspective SQL; never use with show."
    )
    no_populate_cache: bool = Field(
        default=False, description="Compile/show/data_test only: skip relation cache discovery."
    )

    def to_request(self) -> DbtRunRequest:
        values = self.model_dump(exclude={"variables"})
        if len({v.name for v in self.variables}) != len(self.variables):
            raise CodingToolAccessDenied("coding_dbt_duplicate_variable")
        return DbtRunRequest.model_validate({
            **values,
            "variables": {v.name: v.value for v in self.variables},
        })


class _ValidateDbtInput(_ToolInput):
    project_subdir: str
    selectors: list[str] = Field(min_length=1, max_length=50)
    show_limit: int = Field(default=5, ge=1, le=5000)


class _RunDbtInput(_ToolInput):
    request: _DbtToolRequest


class _RunQueryInput(_ToolInput):
    request: CodingDatabaseQueryRequest


class _ReadToolResultInput(_ToolInput):
    field: Literal["all", "stdout", "stderr", "dbt_receipt", "content", "diff"] = "all"
    call_id: str = Field(min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=4_000, ge=1, le=4_000)


class _CandidateDiffInput(_ToolInput):
    path: str | None = Field(
        default=None,
        max_length=1024,
        description="One exact changed file path, or null for all changes. Directories and globs are not accepted.",
    )
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=16_000, ge=1, le=16_000)


class _TaskStatusInput(_ToolInput):
    pass


_INPUTS: dict[CodingToolName, type[_ToolInput]] = {
    "repository_read": _RepositoryReadInput,
    "repository_search": _RepositorySearchInput,
    "repository_apply_patch": _RepositoryPatchInput,
    "repository_run_command": _RepositoryCommandInput,
    "workspace_skill_read": _WorkspaceSkillReadInput,
    "run_dbt": _RunDbtInput,
    "validate_dbt": _ValidateDbtInput,
    "run_query": _RunQueryInput,
    "task_status": _TaskStatusInput,
    "candidate_diff": _CandidateDiffInput,
    "read_tool_result": _ReadToolResultInput,
}

_CAPABILITY_TOOLS: dict[CodingTeamMemberCapability, tuple[CodingToolName, ...]] = {
    CodingTeamMemberCapability.CANDIDATE: (
        "repository_read",
        "repository_search",
        "repository_apply_patch",
        "repository_run_command",
        "workspace_skill_read",
        "run_dbt",
        "validate_dbt",
        "run_query",
        "task_status",
        "read_tool_result",
    ),
    CodingTeamMemberCapability.REVIEW: (
        "candidate_diff",
        "repository_read",
        "repository_search",
        "repository_run_command",
        "workspace_skill_read",
        "run_dbt",
        "validate_dbt",
        "run_query",
        "task_status",
        "read_tool_result",
    ),
}

_TOOL_DESCRIPTIONS: dict[CodingToolName, str] = {
    "candidate_diff": "Read the authoritative base-to-candidate diff. Omit path for all changed files or select one changed path. Offsets are characters; continue only if truncated. Use this to verify change scope: sandbox git status/log describe a clean snapshot, not this diff.",
    "repository_read": "Read UTF-8 text by character offset, up to 16000 characters. Start at offset 0; if truncated, continue from next_offset until next_offset equals total_chars. Prefer one full read over many small fragments. Missing or non-text files return exit_code 1 with a reason.",
    "repository_search": "Search file contents using a ripgrep regular expression; exit_code 1 means no matches. Use path_prefix '.' for the root, or JSON null for no prefix.",
    "repository_apply_patch": "Apply a unified git diff to repository-relative paths. To create models/test_model.sql: \ndiff --git a/models/test_model.sql b/models/test_model.sql\nnew file mode 100644\n--- /dev/null\n+++ b/models/test_model.sql\n@@ -0,0 +1 @@\n+select 1 as id\n. Inspect exit_code and stderr; Git apply rejects invalid patches atomically; an index refresh error can occur after files were written. Use git diff HEAD to verify changes.",
    "repository_run_command": "Run a bounded offline command. Allowed: git status/diff/ls-files/show/log, rg, ls, cat, python -m unittest/pytest/compileall, pytest. Use repository_read instead of sed. Shell, network, installers and direct dbt commands are unavailable.",
    "workspace_skill_read": "Read a selected version-pinned skill. Start at offset=0 with its main SKILL.md: its response includes available_files with exact paths, types and sizes for this bundle version. Choose a referenced asset from that catalog only when it resolves a specific need. Paginate a selected file only when truncated=true, using next_offset. Do not reread content already available.",
    "validate_dbt": "Run focused read-only dbt parse, list, compile and show sequentially for the selected candidate models in one call. Compile skips introspection and relation cache; show permits read-only introspection. Stops at the first failure. Each command gets its own saved receipt and review evidence. Prefer this for the standard check suite; use run_dbt for a targeted retry or checks requiring different flags.",
    "run_dbt": "Run typed dbt in the admitted project. list returns JSON metadata (unique_id, name, original_file_path, description, columns, config) for selected nodes; use it instead of reading target/manifest.json. Use selectors for the changed model, not the entire project. For compile, prefer no_introspect=true and no_populate_cache=true unless its macros require live metadata. For show keep no_introspect=false. Parse/list require both flags false. report blocked warehouse checks; do not repeatedly retry unchanged failures.",
    "run_query": "Execute a bounded read-only governed database query.",
    "task_status": "Read compact current status for this task.",
    "read_tool_result": "Read a saved result only when a required fact is missing. Select field=stdout, stderr, dbt_receipt, content or diff to avoid paging JSON wrappers; all returns the complete JSON. Offsets are characters. Successful visible evidence does not need re-reading.",
}


class CodingToolGateway(Protocol):
    async def invoke(
        self,
        *,
        name: CodingToolName,
        task: AgentTask,
        principal: ExecutionPrincipal,
        arguments: _ToolInput,
    ) -> dict[str, Any]: ...


class CodingToolAuthorizationService:
    """Live server-side authorization repeated for every SDK function call."""

    def __init__(
        self,
        *,
        runs: CodingRunRepository,
        users: UserAccountRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
    ) -> None:
        self._runs = runs
        self._users = users
        self._workspaces = workspaces
        self._agents = agents

    def authorize(
        self,
        *,
        principal: ExecutionPrincipal,
        capability: CodingTeamMemberCapability,
        name: CodingToolName,
    ) -> AgentTask:
        if name not in _CAPABILITY_TOOLS[capability]:
            raise CodingToolAccessDenied("coding_tool_not_allowed_for_capability")
        if principal.auth_kind != "session" or principal.task_id is None:
            raise CodingToolAccessDenied("coding_tool_web_context_required")
        user = self._users.get_by_id(user_id=principal.actor_user_id)
        if user is None or not user.is_active:
            raise CodingToolAccessDenied("coding_tool_actor_unavailable")
        if (
            self._workspaces.membership(
                workspace_id=principal.workspace_id,
                user_id=principal.actor_user_id,
            )
            is None
        ):
            raise CodingToolAccessDenied("coding_tool_workspace_access_denied")
        task = self._runs.get_task(
            task_id=principal.task_id,
            workspace_id=principal.workspace_id,
            owner_user_id=principal.actor_user_id,
        )
        if (
            task.run_id != principal.run_id
            or task.agent_profile_id != principal.executing_agent_id
            or task.capability != capability
        ):
            raise CodingToolAccessDenied("coding_tool_principal_mismatch")
        if task.status in {
            CodingRunStatus.CANCEL_REQUESTED,
            CodingRunStatus.CANCELLED,
            CodingRunStatus.FAILED,
            CodingRunStatus.REJECTED,
            CodingRunStatus.SUCCEEDED,
        }:
            raise CodingToolAccessDenied("coding_tool_task_not_active")
        if (
            self._agents.get(
                agent_id=task.agent_profile_id,
                workspace_id=task.workspace_id,
                current_user_id=task.owner_user_id,
                active_only=True,
            )
            is None
        ):
            raise CodingToolAccessDenied("coding_tool_agent_unavailable")
        return task


class CodingSpecialistAgentFactory:
    """Compose native SDK agents from the authorized specialist tool set."""

    def __init__(
        self,
        *,
        authorizer: CodingToolAuthorizationService,
        gateway: CodingToolGateway,
    ) -> None:
        self._authorizer = authorizer
        self._gateway = gateway
        from rhetorica_backend.domain.coding.tool_output import CodingToolOutputSerializer

        self._outputs = CodingToolOutputSerializer()

    def build(
        self,
        *,
        capability: CodingTeamMemberCapability,
        principal: ExecutionPrincipal,
        model: Model | str,
        timeout_seconds: float,
    ) -> Agent[Any]:
        tools = [
            self._tool(
                capability=capability,
                principal=principal,
                name=name,
                timeout_seconds=timeout_seconds,
            )
            for name in _CAPABILITY_TOOLS[capability]
        ]
        return Agent(
            name=f"Rhetorica coding {capability.value}",
            instructions=(
                "Use only the provided typed tools. Never request credentials, "
                "network endpoints, raw connector configuration or a generic tool. "
                "Read root AGENTS.md directly first; repository_search searches contents, not filenames. "
                "Use ls or rg --files for file discovery. Request independent file/skill reads together in the same "
                "turn when multiple tool calls are enabled. Never batch a mutation with a check that depends on it. Stop exploring once the edit "
                "and relevant validation are clear. For a small change, inspect repository guidance, "
                "the project configuration and one relevant example, then implement. "
                "Each further search should resolve a concrete uncertainty that affects the change. "
                "Do not survey unrelated folders or enumerate all configuration variants. "
                "Prefer existing project defaults; do not invent optional configuration. "
                "After the requested change and focused checks succeed, inspect the diff once "
                "and finish with paths, actual results and limitations. Re-run checks only after "
                "a relevant edit or failure. Tool receipts are authoritative; do not re-fetch "
                "successful results just to restate them. Match each acceptance criterion to evidence; "
                "when all are covered, return the result immediately. Further exploration must resolve "
                "a named missing criterion or contradiction. Use targeted file discovery, never a full "
                "repository listing when the task already supplies paths. Successful compact check "
                "receipts suffice; full logs are for investigating errors or missing facts."
            ),
            model=model,
            tools=cast(Any, tools),
        )

    def _tool(
        self,
        *,
        capability: CodingTeamMemberCapability,
        principal: ExecutionPrincipal,
        name: CodingToolName,
        timeout_seconds: float,
    ) -> FunctionTool:
        async def invoke(_sdk_context: Any, arguments_json: str) -> str:
            task = self._authorizer.authorize(principal=principal, capability=capability, name=name)
            try:
                arguments = _INPUTS[name].model_validate_json(arguments_json)
            except ValidationError as error:
                return json.dumps({
                    "status": "invalid_arguments",
                    "reason_code": "coding_tool_input_invalid",
                    "errors": error.errors(
                        include_input=False, include_url=False, include_context=False
                    ),
                    "instruction": "Correct the arguments using the tool schema and call again.",
                })
            arguments._call_id = getattr(_sdk_context, "tool_call_id", None)
            result = await self._gateway.invoke(
                name=name,
                task=task,
                principal=principal,
                arguments=arguments,
            )
            # Skill pages are bounded at the reader; a second truncation would hide
            # instructions and force paging through the JSON receipt instead.
            if name in {"workspace_skill_read", "candidate_diff"}:
                return self._outputs.encode(result)
            return self._outputs.serialize(result, call_id=arguments._call_id)

        return FunctionTool(
            name=name,
            description=_TOOL_DESCRIPTIONS[name],
            params_json_schema=ensure_strict_json_schema(_INPUTS[name].model_json_schema()),
            strict_json_schema=True,
            on_invoke_tool=invoke,
            timeout_seconds=timeout_seconds,
        )
