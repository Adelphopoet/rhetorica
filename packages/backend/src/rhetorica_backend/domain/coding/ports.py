"""Narrow ports for governed coding infrastructure."""

from __future__ import annotations

from typing import IO, Protocol

from rhetorica_backend.contracts.coding import (
    AgentGovernedPolicy,
    CandidateManifest,
    CodingDatabaseQueryRequest,
    CodingIterationReservation,
    CodingRun,
    DefaultAssistantPolicy,
    EnvironmentProfile,
    ExecutionPrincipal,
    GovernedAgentSettings,
    PublicationReceipt,
    QueryCapabilityClaims,
    SandboxArtifactManifest,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxPhase,
    SandboxTaskLease,
    ValidationReport,
)


class RepositorySnapshotPort(Protocol):
    def checkout(self, *, run: CodingRun, principal: ExecutionPrincipal) -> str: ...
    def create_candidate(
        self, *, snapshot_id: str, run: CodingRun, principal: ExecutionPrincipal
    ) -> CandidateManifest: ...


class SandboxPort(Protocol):
    def create(
        self,
        *,
        run_id: str,
        task_id: str,
        profile: EnvironmentProfile,
        principal: ExecutionPrincipal,
        phase: SandboxPhase = SandboxPhase.RUNTIME,
    ) -> SandboxTaskLease: ...
    def stage_snapshot(
        self, *, lease_id: str, snapshot_id: str, principal: ExecutionPrincipal
    ) -> None: ...
    def execute(
        self,
        *,
        lease_id: str,
        request: SandboxExecutionRequest,
        principal: ExecutionPrincipal,
    ) -> SandboxExecutionResult: ...
    def collect(
        self, *, lease_id: str, principal: ExecutionPrincipal
    ) -> SandboxArtifactManifest: ...
    def checkpoint(self, *, lease_id: str, principal: ExecutionPrincipal) -> str: ...
    def close(self, *, lease_id: str, principal: ExecutionPrincipal) -> None: ...
    def cancel(self, *, lease_id: str, principal: ExecutionPrincipal) -> None: ...


class ArtifactPort(Protocol):
    def verify_owned(self, *, artifact_id: str, workspace_id: str, owner_user_id: str) -> bool: ...
    def open_owned(
        self, *, artifact_id: str, workspace_id: str, owner_user_id: str
    ) -> IO[bytes]: ...


class DatabaseQueryPort(Protocol):
    def execute(
        self, *, claims: QueryCapabilityClaims, request: CodingDatabaseQueryRequest
    ) -> dict[str, object]: ...


class TrinoProtocolPort(Protocol):
    def statement(
        self,
        *,
        claims: QueryCapabilityClaims,
        sql: str,
        client_headers: dict[str, str],
    ) -> dict[str, object]: ...
    def poll(self, *, claims: QueryCapabilityClaims, query_handle: str) -> dict[str, object]: ...
    def cancel(self, *, claims: QueryCapabilityClaims, query_handle: str) -> None: ...


class CodingPolicyPort(Protocol):
    def authorize(self, *, principal: ExecutionPrincipal, operation: str) -> None: ...


class AgentPolicyAdministrationPort(Protocol):
    def get_agent_policy(
        self, *, workspace_id: str, agent_id: str
    ) -> AgentGovernedPolicy | None: ...
    def replace_agent_policy(
        self,
        *,
        workspace_id: str,
        agent_id: str,
        actor_user_id: str,
        settings: GovernedAgentSettings,
    ) -> AgentGovernedPolicy: ...
    def get_default_assistant_policy(self, *, workspace_id: str) -> DefaultAssistantPolicy: ...
    def replace_default_assistant_policy(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        settings: GovernedAgentSettings,
    ) -> DefaultAssistantPolicy: ...


class BudgetLedgerPort(Protocol):
    def reserve_iteration(
        self,
        *,
        run_id: str,
        task_id: str,
        executing_agent_profile_id: str,
        invocation_id: str,
        principal: ExecutionPrincipal,
    ) -> CodingIterationReservation: ...


class PublicationPort(Protocol):
    def publish(
        self,
        *,
        run_id: str,
        candidate: CandidateManifest,
        report: ValidationReport,
        principal: ExecutionPrincipal,
    ) -> PublicationReceipt: ...
