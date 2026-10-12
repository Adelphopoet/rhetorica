"""Lifecycle-level publication decision before the GitLab publisher is invoked."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from rhetorica_backend.contracts.coding import (
    CodingReasonCode,
    CodingRun,
    ValidationReport,
    ValidationStatus,
)


@dataclass(frozen=True)
class PublicationDecision:
    allowed: bool
    reason_code: CodingReasonCode | None = None
    limitation_names: tuple[str, ...] = ()
    missing_reviews: tuple[str, ...] = ()


class CodingPublicationDecisionService:
    """Fail closed before a worker asks the G publisher to mutate GitLab."""

    _BLOCKING_REASONS: ClassVar[set[CodingReasonCode]] = {
        CodingReasonCode.ITERATION_LIMIT_REACHED,
        CodingReasonCode.RUNTIME_LIMIT_REACHED,
        CodingReasonCode.EXECUTION_FAILED,
        CodingReasonCode.CODING_DISABLED,
    }
    _MATERIALIZATION_CHECKS: ClassVar[set[str]] = {"dbt_materialization", "materialization"}

    def decide(self, *, run: CodingRun, report: ValidationReport) -> PublicationDecision:
        if run.candidate is None or run.candidate.digest != report.candidate_digest:
            return PublicationDecision(False, CodingReasonCode.POLICY_DENIED)
        if run.reason_code in self._BLOCKING_REASONS:
            return PublicationDecision(False, run.reason_code)
        if run.status in {"cancel_requested", "cancelled", "failed", "rejected"}:
            return PublicationDecision(False, CodingReasonCode.POLICY_DENIED)
        missing = missing_required_reviews(run, report)
        if missing:
            return PublicationDecision(
                False, CodingReasonCode.POLICY_DENIED, missing_reviews=missing
            )
        mandatory = [check for check in report.checks if check.mandatory]
        if not mandatory:
            return PublicationDecision(False, CodingReasonCode.POLICY_DENIED)
        limitations: list[str] = []
        for check in mandatory:
            if check.status == ValidationStatus.PASSED:
                continue
            if (
                check.status == ValidationStatus.NOT_RUN
                and check.name in self._MATERIALIZATION_CHECKS
            ):
                limitations.append(check.name)
                continue
            return PublicationDecision(False, CodingReasonCode.POLICY_DENIED)
        return PublicationDecision(True, limitation_names=tuple(sorted(limitations)))


def missing_required_reviews(run: CodingRun, report: ValidationReport) -> tuple[str, ...]:
    if run.team_snapshot is None:
        return ()
    passed = {check.name for check in report.checks if check.status == ValidationStatus.PASSED}
    return tuple(
        f"independent_review:{member.id}"
        for member in run.team_snapshot.members
        if member.required_review and f"independent_review:{member.id}" not in passed
    )
