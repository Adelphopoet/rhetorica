"""Governed coding contracts shared by API, workflows and infrastructure adapters."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rhetorica_backend.contracts.execution import EngineType
from rhetorica_backend.contracts.workspace_skills import WorkspaceSkillVersionBinding


class CodingContract(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


MAX_POLICY_SCOPES = 100
MAX_POLICY_BRANCHES = 500
MAX_POLICY_PATHS = 1_000


class GitLabWriteScope(CodingContract):
    connector_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=255)
    target_branches: list[Annotated[str, Field(max_length=255)]] = Field(
        min_length=1, max_length=50
    )
    allowed_paths: list[Annotated[str, Field(max_length=1024)]] = Field(
        min_length=1, max_length=200
    )

    @field_validator("connector_id", "project_id")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("gitlab_scope_identity_required")
        if "\x00" in normalized:
            raise ValueError("gitlab_scope_identity_invalid")
        return normalized

    @field_validator("target_branches")
    @classmethod
    def normalize_branches(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("gitlab_target_branch_required")
        if any(
            "\x00" in branch
            or branch.startswith(("/", "-", "."))
            or branch.endswith(("/", ".", ".lock"))
            or ".." in branch
            or "@{" in branch
            or "//" in branch
            or any(character.isspace() or ord(character) < 32 for character in branch)
            or any(character in "~^:?*[]" for character in branch)
            or "\\" in branch
            for branch in normalized
        ):
            raise ValueError("gitlab_target_branch_invalid")
        return normalized

    @field_validator("allowed_paths")
    @classmethod
    def normalize_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if not candidate:
                continue
            if (
                "\x00" in candidate
                or "\\" in candidate
                or candidate.startswith("/")
                or candidate in {".", ".."}
            ):
                raise ValueError("gitlab_allowed_path_invalid")
            path = PurePosixPath(candidate.rstrip("/"))
            if any(part in {"", ".", "..", ".git"} for part in path.parts):
                raise ValueError("gitlab_allowed_path_invalid")
            canonical = path.as_posix()
            if canonical not in normalized:
                normalized.append(canonical)
        if not normalized:
            raise ValueError("gitlab_allowed_path_required")
        return normalized


class AgentDelegationGrant(CodingContract):
    subagent_id: str = Field(min_length=1, max_length=36)
    gitlab_write_scopes: list[GitLabWriteScope] = Field(min_length=1, max_length=100)

    @field_validator("subagent_id")
    @classmethod
    def normalize_subagent_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("subagent_id_required")
        return normalized


class GovernedAgentSettings(CodingContract):
    revision: int = Field(default=1, ge=1)
    gitlab_write_scopes: list[GitLabWriteScope] = Field(default_factory=list, max_length=100)
    allowed_subagent_ids: list[str] = Field(default_factory=list, max_length=100)
    delegation_grants: list[AgentDelegationGrant] = Field(default_factory=list, max_length=100)
    max_iterations: int = Field(default=100, ge=1)
    max_runtime_seconds: int = Field(default=1200, ge=1)

    @field_validator("allowed_subagent_ids")
    @classmethod
    def normalize_subagents(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("gitlab_write_scopes")
    @classmethod
    def normalize_scopes(cls, value: list[GitLabWriteScope]) -> list[GitLabWriteScope]:
        normalized: list[GitLabWriteScope] = []
        seen: set[str] = set()
        for scope in value:
            identity = scope.model_dump_json()
            if identity not in seen:
                seen.add(identity)
                normalized.append(scope)
        return normalized

    @model_validator(mode="after")
    def validate_delegation_graph(self) -> GovernedAgentSettings:
        ids = [grant.subagent_id for grant in self.delegation_grants]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_agent_delegation_grant")
        missing = set(ids) - set(self.allowed_subagent_ids)
        if missing:
            raise ValueError("delegation_grant_requires_allowed_subagent")
        scopes = self.gitlab_write_scopes + [
            scope for grant in self.delegation_grants for scope in grant.gitlab_write_scopes
        ]
        if len(scopes) > MAX_POLICY_SCOPES:
            raise ValueError("agent_policy_scope_limit_exceeded")
        if sum(len(scope.target_branches) for scope in scopes) > MAX_POLICY_BRANCHES:
            raise ValueError("agent_policy_branch_limit_exceeded")
        if sum(len(scope.allowed_paths) for scope in scopes) > MAX_POLICY_PATHS:
            raise ValueError("agent_policy_path_limit_exceeded")
        return self


class DefaultAssistantPolicy(GovernedAgentSettings):
    workspace_id: str


class AgentGovernedPolicy(GovernedAgentSettings):
    workspace_id: str
    agent_id: str = Field(min_length=1, max_length=36)


class UpdateAgentGovernedPolicyRequest(GovernedAgentSettings):
    """Full versioned replacement used only by system-admin APIs."""


class AgentPolicyConflict(CodingContract):
    reason_code: Literal["policy_revision_conflict"] = "policy_revision_conflict"
    current_revision: int = Field(ge=1)


class AgentPolicySnapshot(CodingContract):
    """Server-created immutable policy inputs captured when a task is admitted."""

    workspace_id: str
    root_agent_id: str | None = None
    executing_agent_id: str
    root_settings: GovernedAgentSettings | None = None
    executing_settings: GovernedAgentSettings
    resource_connector_id: str
    run_scope: GitLabWriteScope


class AgentPolicyRecord(CodingContract):
    agent_id: str = Field(min_length=1, max_length=36)
    settings: GovernedAgentSettings = Field(default_factory=GovernedAgentSettings)


class AgentPolicyGraph(CodingContract):
    """Validation boundary used by admin saves; runtime never infers delegation edges."""

    agents: list[AgentPolicyRecord]

    @model_validator(mode="after")
    def validate_graph(self) -> AgentPolicyGraph:
        records = {record.agent_id: record for record in self.agents}
        if len(records) != len(self.agents):
            raise ValueError("duplicate_agent_policy")
        known_ids = set(records)
        edges: dict[str, set[str]] = {}
        for agent_id, record in records.items():
            allowed = set(record.settings.allowed_subagent_ids)
            if agent_id in allowed:
                raise ValueError("agent_self_delegation_forbidden")
            if allowed - known_ids:
                raise ValueError("unknown_allowed_subagent")
            edges[agent_id] = allowed

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(agent_id: str) -> None:
            if agent_id in visiting:
                raise ValueError("agent_delegation_cycle_forbidden")
            if agent_id in visited:
                return
            visiting.add(agent_id)
            for child_id in edges[agent_id]:
                visit(child_id)
            visiting.remove(agent_id)
            visited.add(agent_id)

        for agent_id in records:
            visit(agent_id)
        return self


class CodingRunPhase(StrEnum):
    PREPARING = "preparing"
    DEVELOPING = "developing"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETED = "completed"


class CodingRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


class CodingReasonCode(StrEnum):
    CODING_DISABLED = "coding_disabled"
    PUBLICATION_NOT_AUTHORIZED = "publication_not_authorized"
    SANDBOX_RUNTIME_UNAVAILABLE = "sandbox_runtime_unavailable"
    BASE_CHANGED = "base_changed"
    GITLAB_CONNECTOR_SCOPE_INSUFFICIENT = "gitlab_connector_scope_insufficient"
    WORKSPACE_ACCESS_DENIED = "workspace_access_denied"
    RESOURCE_ACCESS_REVOKED = "resource_access_revoked"
    CHANNEL_NOT_ALLOWED = "channel_not_allowed"
    POLICY_DENIED = "policy_denied"
    POLICY_REFERENCE_INVALID = "policy_reference_invalid"
    POLICY_REVISION_CONFLICT = "policy_revision_conflict"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    RUNTIME_LIMIT_REACHED = "runtime_limit_reached"
    EXECUTION_FAILED = "execution_failed"
    DEPENDENCY_INSTALL_FAILED = "coding_dependency_install_failed"
    CANCELLED_BY_OWNER = "cancelled_by_owner"
    COORDINATOR_STOPPED = "coordinator_stopped"


class PublicationPolicyDecision(CodingContract):
    allowed: bool
    reason_code: CodingReasonCode | None = None
    effective_scopes: list[GitLabWriteScope] = Field(default_factory=list)
    policy_revisions: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision(self) -> PublicationPolicyDecision:
        if self.allowed and not self.effective_scopes:
            raise ValueError("allowed_publication_requires_effective_scope")
        if not self.allowed and self.reason_code is None:
            raise ValueError("denied_publication_requires_reason")
        return self


class RepositoryBinding(CodingContract):
    gitlab_connector_id: str
    project_id: str
    target_branch: str
    base_sha: str = Field(pattern=r"^[a-fA-F0-9]{40,64}$")


class DataWarehouseBinding(CodingContract):
    connector_id: str
    dbt_project_subdir: str = "."
    catalog: str | None = None
    schema_name: str | None = None


class DbtCommand(StrEnum):
    PARSE = "parse"
    LIST = "list"
    COMPILE = "compile"
    SHOW = "show"
    DATA_TEST = "data_test"


class DbtEvidenceScope(StrEnum):
    CANDIDATE = "candidate"
    BASELINE = "baseline"


class DbtRunRequest(CodingContract):
    """Model-safe dbt request. Connection details are deliberately absent."""

    command: DbtCommand
    project_subdir: str = "."
    selectors: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        default_factory=list, max_length=50
    )
    excludes: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        default_factory=list, max_length=50
    )
    variables: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=100)
    show_limit: int | None = Field(default=None, ge=1, le=5_000)
    threads: int = Field(default=1, ge=1, le=8)
    no_introspect: bool = Field(
        default=False, description="Only compile: skip introspective SQL; never use with show."
    )
    no_populate_cache: bool = Field(
        default=False, description="Compile/show/data_test only: skip relation cache discovery."
    )

    @field_validator("project_subdir")
    @classmethod
    def validate_project_subdir(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or normalized == ".."
            or any(part in {"", ".."} for part in path.parts)
        ):
            raise ValueError("dbt_project_path_invalid")
        return path.as_posix()

    @field_validator("selectors", "excludes")
    @classmethod
    def validate_selectors(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(item.startswith("-") or "\x00" in item for item in normalized):
            raise ValueError("dbt_selector_invalid")
        return normalized

    @model_validator(mode="after")
    def validate_command_options(self) -> DbtRunRequest:
        if self.command == DbtCommand.SHOW:
            if len(self.selectors) != 1:
                raise ValueError("dbt_show_requires_one_selector")
            if self.show_limit is None:
                raise ValueError("dbt_show_limit_required")
        elif self.show_limit is not None:
            raise ValueError("dbt_show_limit_not_allowed")
        if self.command == DbtCommand.PARSE and (self.selectors or self.excludes):
            raise ValueError("dbt_parse_selector_not_allowed")
        if self.no_introspect and self.command != DbtCommand.COMPILE:
            raise ValueError("dbt_no_introspect_requires_compile")
        if self.no_populate_cache and self.command not in {
            DbtCommand.COMPILE,
            DbtCommand.SHOW,
            DbtCommand.DATA_TEST,
        }:
            raise ValueError("dbt_no_populate_cache_not_supported")
        return self


class DbtRuntimeProfile(CodingContract):
    """Trusted gateway profile injected after tool authorization."""

    profile_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    profiles_yml: str = Field(min_length=2, max_length=64_000)
    expires_at: datetime


class DbtCommandReceipt(CodingContract):
    id: str
    command: DbtCommand
    status: ValidationStatus
    evidence_scope: DbtEvidenceScope
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    incomplete: bool = False
    reason: str | None = None
    stdout_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    materialization_status: Literal["not_run"] = "not_run"


class ExecutionPrincipal(CodingContract):
    actor_user_id: str
    workspace_id: str
    entry_source: Literal["web_chat", "web_scenario"]
    auth_kind: Literal["session"] = "session"
    root_agent_id: str | None = None
    executing_agent_id: str | None = None
    parent_agent_id: str | None = None
    run_id: str
    task_id: str | None = None


class QueryCapabilityClaims(CodingContract):
    version: Literal[1] = 1
    principal: ExecutionPrincipal
    connector_id: str = Field(min_length=1, max_length=36)
    connector_alias: str = Field(min_length=1, max_length=128)
    engine: EngineType
    endpoint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    issued_at: datetime
    expires_at: datetime
    max_result_bytes: int = Field(ge=1, le=1024 * 1024 * 1024)
    max_concurrency: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def validate_query_capability(self) -> QueryCapabilityClaims:
        if self.principal.task_id is None:
            raise ValueError("query_capability_task_required")
        if self.expires_at <= self.issued_at:
            raise ValueError("query_capability_expiry_invalid")
        return self


class IssueDbtProfileRequest(CodingContract):
    principal: ExecutionPrincipal
    connector_id: str = Field(min_length=1, max_length=36)
    profile_name: str = Field(default="rhetorica", pattern=r"^[A-Za-z0-9_-]+$")
    expires_at: datetime
    threads: int = Field(default=1, ge=1, le=8)
    max_result_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_concurrency: int = Field(default=2, ge=1, le=8)


class IssueQueryCapabilityRequest(CodingContract):
    """Issue a task-scoped capability for any supported database connector."""

    principal: ExecutionPrincipal
    connector_id: str = Field(min_length=1, max_length=36)
    expires_at: datetime
    max_result_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_concurrency: int = Field(default=2, ge=1, le=8)


class IssuedQueryCapability(CodingContract):
    connector_id: str
    connector_alias: str
    engine: EngineType
    capability: str = Field(min_length=1)
    query_endpoint: str = Field(min_length=1)


class CodingDatabaseQueryRequest(CodingContract):
    sql: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    row_limit: int = Field(default=500, ge=1, le=500)
    timeout_ms: int = Field(default=120_000, ge=100, le=900_000)


class IssuedDbtProfile(CodingContract):
    connector_id: str
    connector_alias: str
    runtime_profile: DbtRuntimeProfile


class PythonDependencyRecipe(CodingContract):
    pip_dependencies: list[str] = Field(default_factory=list, max_length=100)
    dependency_sources: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("pip_dependencies")
    @classmethod
    def pinned_python_packages(cls, values: list[str]) -> list[str]:
        import re

        result, names = [], set()
        for value in values:
            value = value.strip()
            match = re.fullmatch(
                r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9][A-Za-z0-9.!+_-]*)",
                value,
            )
            if not match or len(value) > 255:
                raise ValueError("coding_pip_dependency_requires_exact_version")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            if name in names:
                raise ValueError("coding_pip_dependency_duplicate")
            names.add(name)
            result.append(value)
        return result

    @field_validator("dependency_sources")
    @classmethod
    def https_package_indexes(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        for value in result:
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("coding_dependency_source_must_use_https_without_credentials")
        return result


class PreparePythonEnvironmentRequest(PythonDependencyRecipe):
    profile_id: str
    profile_revision: int = Field(ge=1)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)


class EnvironmentProfile(PythonDependencyRecipe):
    id: str
    revision: int = Field(ge=1)
    image_digest: str = Field(min_length=1)
    runtime_class_name: str = Field(min_length=1)
    tool_versions: dict[str, str] = Field(default_factory=dict)
    max_runtime_seconds: int = Field(ge=1)
    cpu_limit: str
    memory_limit: str
    disk_limit_bytes: int = Field(ge=1)
    pid_limit: int = Field(ge=1)
    output_limit_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    software_manifest_digest: str = Field(default="", pattern=r"^(|[a-f0-9]{64})$")
    dependency_sources: list[str] = Field(default_factory=list, max_length=20)
    created_at: datetime

    @field_validator("image_digest")
    @classmethod
    def require_pinned_image(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.endswith(tuple("0123456789abcdef")) or "@sha256:" not in normalized:
            raise ValueError("coding_environment_image_must_be_digest_pinned")
        digest = normalized.rsplit("@sha256:", 1)[1]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("coding_environment_image_must_be_digest_pinned")
        return normalized

    @field_validator("runtime_class_name")
    @classmethod
    def require_runtime_class(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coding_runtime_class_required")
        return normalized


class UpsertEnvironmentProfileRequest(PythonDependencyRecipe):
    """Admin-editable resource envelope; deployed runtime identity stays server-owned."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    expected_revision: int = Field(default=0, ge=0)
    max_runtime_seconds: int = Field(ge=60, le=86_400)
    cpu_limit: str = Field(pattern=r"^([1-9][0-9]*m|[1-9][0-9]*)$")
    memory_limit: str = Field(pattern=r"^[1-9][0-9]*(Mi|Gi)$")
    disk_limit_bytes: int = Field(ge=64 * 1024 * 1024, le=1024**4)
    pid_limit: int = Field(ge=16, le=4096)
    output_limit_bytes: int = Field(ge=1024, le=1024**3)
    dependency_sources: list[str] = Field(default_factory=list, max_length=20)


class SandboxLeaseStatus(StrEnum):
    CREATED = "created"
    STAGED = "staged"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    CANCEL_REQUESTED = "cancel_requested"
    CLOSED = "closed"
    EXPIRED = "expired"
    FAILED = "failed"


class SandboxPhase(StrEnum):
    RUNTIME = "runtime"
    DEPENDENCIES = "dependencies"


class SandboxTaskLease(CodingContract):
    id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    task_id: str
    environment_profile_id: str
    environment_profile_revision: int = Field(ge=1)
    image_digest: str
    runtime_class_name: str
    tool_versions: dict[str, str] = Field(default_factory=dict)
    software_manifest_digest: str = Field(default="", pattern=r"^(|[a-f0-9]{64})$")
    phase: SandboxPhase = SandboxPhase.RUNTIME
    job_name: str
    status: SandboxLeaseStatus
    attempt: int = Field(default=1, ge=1)
    version: int = Field(default=1, ge=1)
    checkpoint_ref: str | None = None
    candidate_artifact_id: str | None = None
    created_at: datetime
    expires_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_scope_and_expiry(self) -> SandboxTaskLease:
        if self.expires_at <= self.created_at:
            raise ValueError("sandbox_lease_expiry_invalid")
        return self


class SandboxLeaseAcquisition(CodingContract):
    lease: SandboxTaskLease
    created: bool


CODING_OPERATION_ARGUMENT_MAX_CHARS = 32_000


class SandboxOperation(StrEnum):
    READ_FILE = "read_file"
    SEARCH = "search"
    APPLY_PATCH = "apply_patch"
    RUN_COMMAND = "run_command"
    PREPARE_DEPENDENCIES = "prepare_dependencies"
    RUN_DBT = "run_dbt"


class SandboxExecutionRequest(CodingContract):
    operation: SandboxOperation
    arguments: dict[str, str | int | bool | list[str]] = Field(default_factory=dict)
    timeout_seconds: int = Field(ge=1)
    dbt_request: DbtRunRequest | None = None
    dbt_runtime_profile: DbtRuntimeProfile | None = None

    @model_validator(mode="after")
    def validate_typed_operation(self) -> SandboxExecutionRequest:
        has_dbt_payload = self.dbt_request is not None or self.dbt_runtime_profile is not None
        if self.operation == SandboxOperation.RUN_DBT:
            if self.dbt_request is None or self.dbt_runtime_profile is None:
                raise ValueError("run_dbt_requires_trusted_profile")
            if self.arguments:
                raise ValueError("run_dbt_arguments_forbidden")
        elif has_dbt_payload:
            raise ValueError("dbt_payload_requires_run_dbt")
        return self


class SandboxExecutionResult(CodingContract):
    offset: int | None = Field(default=None, ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    total_chars: int | None = Field(default=None, ge=0)
    operation: SandboxOperation
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    duration_ms: int = Field(ge=0)
    dbt_receipt: DbtCommandReceipt | None = None


class SandboxArtifactManifest(CodingContract):
    artifact_id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    task_id: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    regular_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class CandidateManifest(CodingContract):
    artifact_id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_sha: str
    changed_paths: list[str] = Field(default_factory=list)
    size_bytes: int = Field(ge=0)
    created_at: datetime


class CodingPartialResult(CodingContract):
    artifact_ids: list[str] = Field(default_factory=list)
    summary: str | None = None
    stopped_reason: CodingReasonCode | None = None


class ValidationCheck(CodingContract):
    name: str
    status: ValidationStatus
    mandatory: bool = True
    reason: str | None = None
    receipt_id: str | None = None
    artifact_id: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ServerValidationEvidence(CodingContract):
    """Compact server-recorded command outcome; output bodies stay in artifacts."""

    id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    task_id: str | None = None
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=128)
    source: Literal["sandbox", "dbt", "system"]
    status: ValidationStatus
    mandatory: bool = True
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    artifact_id: str | None = Field(default=None, max_length=2048)
    reason: str | None = Field(default=None, max_length=512)
    created_at: datetime


class QaVerdict(CodingContract):
    id: str
    qa_agent_id: str = Field(min_length=1, max_length=36)
    task_id: str = Field(min_length=1, max_length=36)
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["passed", "failed", "blocked"]
    reason: str | None = Field(default=None, max_length=512)
    created_at: datetime


class ValidationReport(CodingContract):
    id: str
    workspace_id: str
    run_id: str
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    checks: list[ValidationCheck] = Field(default_factory=list)
    qa_agent_id: str
    qa_verdict: QaVerdict | None = None
    created_at: datetime


class PublicationReceipt(CodingContract):
    id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    candidate_digest: str
    validation_report_digest: str
    state: Literal["reserved", "commit_recorded", "mr_recorded", "reconcile_required"]
    branch_name: str
    commit_sha: str | None = None
    merge_request_url: str | None = None
    reason_code: CodingReasonCode | None = None
    detail: str | None = Field(default=None, max_length=512)
    attempt: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class SkillReadAddress(CodingContract):
    """A durable pointer to a bounded skill fragment, never its content."""

    skill_id: str = Field(min_length=1, max_length=36)
    version_digest: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    offset: int = Field(ge=0)
    max_chars: int = Field(ge=1, le=24_000)

    @field_validator("path")
    @classmethod
    def validate_skill_path(cls, value: str) -> str:
        normalized = value.strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or "\\" in normalized
            or "\x00" in normalized
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("workspace_skill_read_path_invalid")
        return path.as_posix()


class CodingTeamMemberCapability(StrEnum):
    """Server-recognised work boundaries; labels never grant a capability."""

    CANDIDATE = "candidate"
    REVIEW = "review"


CODING_TEAM_MEMBER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,35}$"


class CodingAgentTeamMember(CodingContract):
    """One non-secret, revision-pinned specialist configured by an administrator."""

    id: str = Field(pattern=CODING_TEAM_MEMBER_ID_PATTERN)
    agent_profile_id: str = Field(min_length=1, max_length=36)
    label: str = Field(min_length=1, max_length=120)
    instruction: str = Field(default="", max_length=12_000)
    capability: CodingTeamMemberCapability
    required_review: bool = False
    skill_version_bindings: list[WorkspaceSkillVersionBinding] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coding_team_member_label_required")
        return normalized

    @property
    def storage_role(self) -> Literal["developer", "qa"]:
        """Map capability to the historical persistence field; labels stay configurable."""

        return "developer" if self.capability == CodingTeamMemberCapability.CANDIDATE else "qa"


def _validate_team_topology(
    coordinator_agent_profile_id: str, members: list[CodingAgentTeamMember]
) -> None:
    ids = [member.id for member in members]
    profiles = [member.agent_profile_id for member in members]
    if len(ids) != len(set(ids)):
        raise ValueError("coding_team_member_id_duplicate")
    if len(profiles) != len(set(profiles)):
        raise ValueError("coding_team_member_profile_duplicate")
    if coordinator_agent_profile_id in profiles:
        raise ValueError("coding_team_coordinator_member_duplicate")
    for member in members:
        if member.required_review and member.capability != CodingTeamMemberCapability.REVIEW:
            raise ValueError("coding_team_required_review_capability")
        if len({binding.skill_id for binding in member.skill_version_bindings}) != len(
            member.skill_version_bindings
        ):
            raise ValueError("coding_team_member_skill_duplicate")


class CodingAgentTeamRevision(CodingContract):
    """Immutable topology which may be selected by a workspace coding preset."""

    id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(min_length=1, max_length=128)
    team_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1)
    coordinator_agent_profile_id: str = Field(min_length=1, max_length=36)
    coordinator_instruction: str = Field(default="", max_length=12_000)
    members: list[CodingAgentTeamMember] = Field(min_length=1, max_length=24)
    is_active: bool = False
    created_at: datetime
    created_by: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_topology(self) -> CodingAgentTeamRevision:
        _validate_team_topology(self.coordinator_agent_profile_id, self.members)
        return self


class CodingAgentTeam(CodingContract):
    id: str = Field(min_length=1, max_length=36)
    workspace_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    active_revision_id: str | None = Field(default=None, max_length=36)
    latest_revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class CodingAgentTeamDetail(CodingContract):
    """Administrator view of one team with its immutable revision history."""

    team: CodingAgentTeam
    revisions: list[CodingAgentTeamRevision] = Field(default_factory=list)


class CreateCodingAgentTeamRevision(CodingContract):
    expected_revision: int = Field(ge=0)
    coordinator_agent_profile_id: str = Field(min_length=1, max_length=36)
    coordinator_instruction: str = Field(default="", max_length=12_000)
    members: list[CodingAgentTeamMember] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_topology(self) -> CreateCodingAgentTeamRevision:
        _validate_team_topology(self.coordinator_agent_profile_id, self.members)
        return self


class UpsertCodingAgentTeamRevision(CreateCodingAgentTeamRevision):
    team_id: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coding_team_name_required")
        return normalized


class CodingAgentTeamSnapshot(CodingContract):
    """Team state admitted into a run. It intentionally contains no policy or secret."""

    team_id: str = Field(min_length=1, max_length=36)
    team_name: str = Field(default="", max_length=120)
    revision_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=1)
    coordinator_agent_profile_id: str = Field(min_length=1, max_length=36)
    coordinator_instruction: str = Field(default="", max_length=12_000)
    members: list[CodingAgentTeamMember] = Field(min_length=1, max_length=24)
    legacy: bool = False

    @model_validator(mode="after")
    def validate_topology(self) -> CodingAgentTeamSnapshot:
        _validate_team_topology(self.coordinator_agent_profile_id, self.members)
        return self

    def member(self, member_id: str) -> CodingAgentTeamMember | None:
        return next((member for member in self.members if member.id == member_id), None)


class CodingCoordinatorDecision(CodingContract):
    """Coordinator's final request; publication admission remains server-owned."""

    decision: Literal["publish", "stop"]
    summary: str

    @field_validator("summary", mode="before")
    @classmethod
    def bound_summary(cls, value: Any) -> Any:
        # A model-written summary is shortened rather than failing a valid decision.
        if not isinstance(value, str):
            return value
        return value.strip()[:2_000] or "No summary provided."


class AgentTask(CodingContract):
    id: str
    workspace_id: str
    owner_user_id: str
    run_id: str
    parent_task_id: str | None = None
    agent_profile_id: str
    role: Literal["coordinator", "developer", "qa", "publisher"]
    team_member_id: str | None = Field(default=None, max_length=36)
    team_member_label: str | None = Field(default=None, max_length=120)
    status: CodingRunStatus
    iterations_used: int = Field(default=0, ge=0)
    max_iterations: int = Field(ge=1)
    max_runtime_seconds: int = Field(ge=1)
    attempts: int = Field(default=0, ge=0)
    instruction_artifact_id: str
    skill_version_digests: list[str] = Field(default_factory=list)
    skill_version_bindings: list[WorkspaceSkillVersionBinding] = Field(default_factory=list)
    skill_read_addresses: list[SkillReadAddress] = Field(default_factory=list)
    result_artifact_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    deadline: datetime | None = None
    checkpoint_ref: str | None = None
    version: int = Field(default=1, ge=1)

    @property
    def capability(self) -> CodingTeamMemberCapability:
        """Decode historical storage roles; runtime policy uses capabilities."""
        if self.role == "developer":
            return CodingTeamMemberCapability.CANDIDATE
        if self.role == "qa":
            return CodingTeamMemberCapability.REVIEW
        raise ValueError("coding_task_has_no_specialist_capability")

    @model_validator(mode="after")
    def validate_budget(self) -> AgentTask:
        if self.iterations_used > self.max_iterations:
            raise ValueError("agent_iteration_budget_exceeded")
        bound_versions = {
            (binding.skill_id, binding.version_digest) for binding in self.skill_version_bindings
        }
        if self.skill_version_bindings and not set(self.skill_version_digests).issubset({
            digest for _, digest in bound_versions
        }):
            raise ValueError("agent_task_skill_digest_binding_mismatch")
        if any(
            (address.skill_id, address.version_digest) not in bound_versions
            for address in self.skill_read_addresses
        ):
            raise ValueError("agent_task_skill_read_address_unbound")
        return self


class CreateCodingDelegationRequest(CodingContract):
    """Server-owned input for creating one developer or QA child task.

    Workspace, owner and limits are derived from the authenticated parent task,
    never accepted from a web or model tool payload.
    """

    run_id: str
    parent_task_id: str
    child_agent_profile_id: str = Field(min_length=1, max_length=36)
    role: Literal["developer", "qa"]
    instruction_artifact_id: str = Field(min_length=1, max_length=2048)
    skill_version_digests: list[str] = Field(default_factory=list)
    skill_version_bindings: list[WorkspaceSkillVersionBinding] = Field(default_factory=list)
    skill_read_addresses: list[SkillReadAddress] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_skill_references(self) -> CreateCodingDelegationRequest:
        bound_versions = {
            (binding.skill_id, binding.version_digest) for binding in self.skill_version_bindings
        }
        if self.skill_version_bindings and not set(self.skill_version_digests).issubset({
            digest for _, digest in bound_versions
        }):
            raise ValueError("agent_task_skill_digest_binding_mismatch")
        if any(
            (address.skill_id, address.version_digest) not in bound_versions
            for address in self.skill_read_addresses
        ):
            raise ValueError("agent_task_skill_read_address_unbound")
        return self


class CodingRun(CodingContract):
    id: str
    workspace_id: str
    owner_user_id: str
    source: Literal["web_chat", "web_scenario"]
    conversation_id: str | None = None
    scenario_id: str | None = None
    root_agent_id: str | None = None
    developer_agent_id: str
    qa_agent_id: str
    team_snapshot: CodingAgentTeamSnapshot | None = None
    idempotency_key: str
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    phase: CodingRunPhase
    status: CodingRunStatus
    reason_code: CodingReasonCode | None = None
    repository: RepositoryBinding
    data_warehouse: DataWarehouseBinding | None = None
    environment_profile_id: str
    environment_profile_revision: int = Field(ge=1)
    policy_revision: int = Field(ge=1)
    candidate: CandidateManifest | None = None
    validation_report_id: str | None = None
    publication_receipt_id: str | None = None
    partial_result: CodingPartialResult | None = None
    created_at: datetime
    deadline: datetime

    @model_validator(mode="after")
    def validate_ownership_and_source(self) -> CodingRun:
        if self.source == "web_chat" and not self.conversation_id:
            raise ValueError("coding_chat_conversation_required")
        if self.source == "web_scenario" and not self.scenario_id:
            raise ValueError("coding_scenario_required")
        if self.deadline <= self.created_at:
            raise ValueError("coding_deadline_must_follow_creation")
        if self.candidate is not None and (
            self.candidate.workspace_id != self.workspace_id
            or self.candidate.owner_user_id != self.owner_user_id
            or self.candidate.run_id != self.id
        ):
            raise ValueError("coding_candidate_ownership_mismatch")
        return self


class CodingRunEvent(CodingContract):
    """A compact owner-visible event; large evidence remains in artifacts."""

    run_id: str
    sequence: int = Field(ge=1)
    type: str = Field(min_length=1, max_length=96)
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CodingRunProgress(CodingContract):
    """Owner-scoped resumable view with references, never result bodies."""

    run_id: str
    workspace_id: str
    cursor: int = Field(ge=0)
    phase: CodingRunPhase
    status: CodingRunStatus
    reason_code: CodingReasonCode | None = None
    events: list[CodingRunEvent] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    budgets: list[CodingBudgetLedger] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class CodingRunOutboxEntry(CodingContract):
    id: str = Field(min_length=1, max_length=128)
    workspace_id: str
    owner_user_id: str
    run_id: str
    kind: Literal["start", "cancel"]
    payload: dict[str, Any] = Field(default_factory=dict)
    delivered: bool = False
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    created_at: datetime


class CodingRunAdmission(CodingContract):
    """Server-owned snapshots required to resume a coding workflow safely."""

    run: CodingRun
    principal: ExecutionPrincipal
    policy_snapshot: AgentPolicySnapshot
    environment_profile: EnvironmentProfile

    @model_validator(mode="after")
    def validate_consistent_scope(self) -> CodingRunAdmission:
        if (
            self.principal.run_id != self.run.id
            or self.principal.workspace_id != self.run.workspace_id
            or self.principal.actor_user_id != self.run.owner_user_id
            or self.policy_snapshot.workspace_id != self.run.workspace_id
            or self.policy_snapshot.executing_agent_id != self.principal.executing_agent_id
            or self.environment_profile.id != self.run.environment_profile_id
            or self.environment_profile.revision != self.run.environment_profile_revision
        ):
            raise ValueError("coding_admission_snapshot_scope_mismatch")
        return self


class CodingRunAdmissionResult(CodingContract):
    run: CodingRun
    created: bool
    workflow_id: str


class CodingResourceSelection(CodingContract):
    """Explicit resource selection; never an authorization grant."""

    dbt_project_subdir: str = Field(default=".", max_length=1024)

    @field_validator("dbt_project_subdir")
    @classmethod
    def validate_project_subdir(cls, value: str) -> str:
        return DbtRunRequest.validate_project_subdir(value)

    root_agent_id: str | None = Field(default=None, max_length=36)
    coding_team_id: str | None = Field(default=None, max_length=36)
    developer_agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    qa_agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    gitlab_connector_id: str = Field(min_length=1, max_length=36)
    project_id: str = Field(min_length=1, max_length=255)
    target_branch: str = Field(min_length=1, max_length=255)
    environment_profile_id: str = Field(min_length=1, max_length=128)
    database_connector_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_team_selection(self) -> CodingResourceSelection:
        if self.coding_team_id is None and (
            self.developer_agent_id is None or self.qa_agent_id is None
        ):
            raise ValueError("coding_team_required")
        return self


class CreateCodingRunRequest(CodingResourceSelection):
    """Browser/model inputs; identity, source, base SHA and limits are server-owned."""

    task: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=36)


class WorkspaceCodingDefaults(CodingContract):
    workspace_id: str
    revision: int = Field(default=0, ge=0)
    resources: CodingResourceSelection | None = None


class UpsertWorkspaceCodingDefaults(CodingContract):
    expected_revision: int = Field(ge=0)
    resources: CodingResourceSelection | None = None


class CodingPlanDraft(CodingContract):
    task: str = Field(min_length=1, max_length=4000)
    steps: list[Annotated[str, Field(min_length=1, max_length=250)]] = Field(
        min_length=1, max_length=12
    )
    developer_brief: str = Field(min_length=1, max_length=2500)
    qa_brief: str = Field(min_length=1, max_length=1500)
    acceptance_criteria: list[Annotated[str, Field(min_length=1, max_length=250)]] = Field(
        min_length=1, max_length=8
    )

    @model_validator(mode="after")
    def bounded_instruction(self) -> CodingPlanDraft:
        if len(self.instruction()) > 12_000:
            raise ValueError("coding_plan_instruction_too_long")
        return self

    def instruction(self) -> str:
        return (
            self.task
            + "\n\nPlan:\n"
            + "\n".join(self.steps)
            + "\n\nCandidate work:\n"
            + self.developer_brief
            + "\n\nIndependent QA review:\n"
            + self.qa_brief
            + "\n\nAcceptance:\n"
            + "\n".join(self.acceptance_criteria)
        )


class CodingChatPlan(CodingContract):
    id: str
    conversation_id: str
    resources: CodingResourceSelection
    draft: CodingPlanDraft
    assistant_message_id: str | None = None
    run_id: str | None = None


class CodingRunView(CodingContract):
    run: CodingRun
    progress: CodingRunProgress
    report: ValidationReport | None = None
    receipt: PublicationReceipt | None = None


class CodingBudgetLedger(CodingContract):
    run_id: str
    budget_owner_agent_id: str
    max_iterations: int = Field(ge=1)
    reserved_iterations: int = Field(ge=0)
    first_attempt_at: datetime | None = None
    deadline: datetime


class CodingIterationReservation(CodingContract):
    id: str
    run_id: str
    budget_owner_agent_id: str
    invocation_id: str
    iteration: int = Field(ge=1)
    deadline: datetime
    reserved_at: datetime
