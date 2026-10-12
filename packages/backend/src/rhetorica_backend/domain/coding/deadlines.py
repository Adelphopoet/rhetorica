"""One deadline calculation for model, tool, subprocess and SQL work."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from rhetorica_backend.contracts.coding import (
    AgentTask,
    CodingPartialResult,
    CodingReasonCode,
    CodingRun,
    EnvironmentProfile,
)
from rhetorica_backend.domain.coding.errors import CodingBudgetExhausted


class CodingStopPort(Protocol):
    def stop_with_partial_result(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        reason_code: str,
        partial_result: CodingPartialResult,
        cancelled: bool,
    ) -> CodingRun: ...


@dataclass(frozen=True)
class CodingOperationDeadline:
    """A non-zero bounded timeout derived from durable run state."""

    expires_at: datetime
    timeout_seconds: int

    @property
    def timeout_ms(self) -> int:
        return self.timeout_seconds * 1_000


class CodingDeadlineCoordinator:
    """Derive and enforce the same ceiling at every external-effect boundary."""

    def operation_deadline(
        self,
        *,
        run: CodingRun,
        task: AgentTask,
        environment: EnvironmentProfile,
        budget_deadline: datetime | None = None,
        now: datetime | None = None,
    ) -> CodingOperationDeadline:
        current = _as_utc(now or datetime.now(tz=UTC))
        candidates = [
            _as_utc(run.deadline),
            current + timedelta(seconds=environment.max_runtime_seconds),
            # A specialist can be stricter than the root budget.  This bound
            # remains mandatory even when the root ledger already has its own
            # first-attempt deadline.
            current + timedelta(seconds=task.max_runtime_seconds),
        ]
        if budget_deadline is not None:
            candidates.append(_as_utc(budget_deadline))
        if task.deadline is not None:
            candidates.append(_as_utc(task.deadline))
        expires_at = min(candidates)
        remaining_seconds = int((expires_at - current).total_seconds())
        if remaining_seconds <= 0:
            raise CodingBudgetExhausted("coding_runtime_limit_reached")
        return CodingOperationDeadline(
            expires_at=expires_at,
            timeout_seconds=max(1, remaining_seconds),
        )

    def cancel_or_expire(
        self,
        *,
        run: CodingRun,
        partial_result: CodingPartialResult,
        reason_code: CodingReasonCode,
        cancelled: bool,
        cancel_handles: tuple[Callable[[], None], ...] = (),
    ) -> CodingRun:
        """Best-effort cancellation never hides the durable terminal result."""

        for cancel in cancel_handles:
            with suppress(OSError, RuntimeError, TimeoutError):
                # A failed remote cancellation must not reopen publication or
                # discard the server-owned partial evidence.
                cancel()
        return self._runs.stop_with_partial_result(
            run_id=run.id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            reason_code=reason_code.value,
            partial_result=partial_result,
            cancelled=cancelled,
        )

    def __init__(self, runs: CodingStopPort) -> None:
        self._runs = runs


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
