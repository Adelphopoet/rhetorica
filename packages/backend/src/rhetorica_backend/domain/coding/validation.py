"""Server-side assembly of validation reports for immutable code candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from rhetorica_backend.contracts.coding import (
    CandidateManifest,
    CodingAgentTeamSnapshot,
    QaVerdict,
    ServerValidationEvidence,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)
from rhetorica_backend.domain.coding.errors import CodingRunConflict


class ValidationEvidencePort(Protocol):
    """Reads receipts recorded by trusted execution activities only."""

    def validation_evidence(
        self,
        *,
        run_id: str,
        workspace_id: str,
        owner_user_id: str,
        candidate_digest: str,
    ) -> list[ServerValidationEvidence]: ...


class ValidationReportAssembler:
    """Builds a publication-grade report without accepting developer prose.

    The developer can produce a candidate and request checks, but only a
    sandbox/dbt/system activity records a receipt. The independent QA verdict
    is bound to that same candidate digest before it is added to the report.
    """

    def __init__(self, evidence: ValidationEvidencePort) -> None:
        self._evidence = evidence

    def assemble(
        self,
        *,
        candidate: CandidateManifest,
        qa_verdict: QaVerdict,
        team: CodingAgentTeamSnapshot | None = None,
    ) -> ValidationReport:
        if qa_verdict.candidate_digest != candidate.digest:
            raise CodingRunConflict("coding_validation_qa_candidate_mismatch")
        evidence = self._evidence.validation_evidence(
            run_id=candidate.run_id,
            workspace_id=candidate.workspace_id,
            owner_user_id=candidate.owner_user_id,
            candidate_digest=candidate.digest,
        )
        if not evidence:
            raise CodingRunConflict("coding_validation_evidence_missing")
        effective_evidence = self._latest_attempt_per_check(evidence)
        checks = [self._receipt_check(item) for item in effective_evidence]
        checks.append(
            ValidationCheck(
                name="independent_qa",
                status=ValidationStatus(qa_verdict.status),
                mandatory=True,
                reason=qa_verdict.reason,
                receipt_id=qa_verdict.id,
            )
        )
        recorded = {check.name for check in checks}
        pending_reviews = [
            ValidationCheck(
                name=f"independent_review:{member.id}",
                status=ValidationStatus.NOT_RUN,
                mandatory=True,
                reason=f"Не выполнена обязательная проверка: {member.label}",
            )
            for member in (team.members if team is not None and not team.legacy else [])
            if member.required_review and f"independent_review:{member.id}" not in recorded
        ]
        checks.extend(pending_reviews)
        checks.sort(key=lambda check: (check.name, check.receipt_id or ""))
        digest = self._digest(
            candidate_digest=candidate.digest,
            evidence=effective_evidence,
            qa_verdict=qa_verdict,
            pending_reviews=pending_reviews,
        )
        return ValidationReport(
            id=str(uuid5(NAMESPACE_URL, f"rhetorica/coding/validation/{digest}")),
            workspace_id=candidate.workspace_id,
            run_id=candidate.run_id,
            candidate_digest=candidate.digest,
            report_digest=digest,
            checks=checks,
            qa_agent_id=qa_verdict.qa_agent_id,
            qa_verdict=qa_verdict,
            created_at=qa_verdict.created_at,
        )

    @staticmethod
    def _receipt_check(evidence: ServerValidationEvidence) -> ValidationCheck:
        return ValidationCheck(
            name=evidence.name,
            status=evidence.status,
            mandatory=evidence.mandatory,
            reason=evidence.reason,
            receipt_id=evidence.id,
            artifact_id=evidence.artifact_id,
            exit_code=evidence.exit_code,
            duration_ms=evidence.duration_ms,
        )

    @staticmethod
    def _latest_attempt_per_check(
        evidence: list[ServerValidationEvidence],
    ) -> list[ServerValidationEvidence]:
        """Use the final receipt for a check while retaining every attempt in audit storage.

        A specialist can correct an invalid command and run the same named check again.
        Publication must judge that final attempt, not a superseded failure.  The receipt
        ID provides a stable tie-breaker for clocks with equal precision.
        """
        latest: dict[str, ServerValidationEvidence] = {}
        for item in evidence:
            current = latest.get(item.name)
            if current is None or (item.created_at, item.id) > (current.created_at, current.id):
                latest[item.name] = item
        return sorted(latest.values(), key=lambda item: (item.name, item.id))

    @staticmethod
    def _digest(
        *,
        candidate_digest: str,
        evidence: list[ServerValidationEvidence],
        qa_verdict: QaVerdict,
        pending_reviews: list[ValidationCheck],
    ) -> str:
        payload = {
            "candidate_digest": candidate_digest,
            "evidence": [
                item.model_dump(mode="json")
                for item in sorted(evidence, key=lambda item: (item.name, item.id))
            ],
            "qa_verdict": qa_verdict.model_dump(mode="json"),
        }
        if pending_reviews:
            payload["pending_reviews"] = [
                check.model_dump(mode="json")
                for check in sorted(pending_reviews, key=lambda check: check.name)
            ]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
