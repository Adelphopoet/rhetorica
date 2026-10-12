from datetime import UTC, datetime, timedelta

from rhetorica_backend.contracts.coding import (
    CandidateManifest,
    QaVerdict,
    ServerValidationEvidence,
)
from rhetorica_backend.domain.coding.validation import ValidationReportAssembler


class _Evidence:
    def __init__(self, values: list[ServerValidationEvidence]) -> None:
        self.values = values

    def validation_evidence(self, **_kwargs) -> list[ServerValidationEvidence]:
        return self.values


def _candidate() -> CandidateManifest:
    return CandidateManifest(
        artifact_id="artifact://candidate",
        workspace_id="workspace",
        owner_user_id="owner",
        run_id="run",
        digest="a" * 64,
        base_sha="b" * 40,
        changed_paths=["models/example.sql"],
        size_bytes=1,
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
    )


def _receipt(*, receipt_id: str, status: str, created_at: datetime) -> ServerValidationEvidence:
    return ServerValidationEvidence(
        id=receipt_id,
        workspace_id="workspace",
        owner_user_id="owner",
        run_id="run",
        task_id="qa-task",
        candidate_digest="a" * 64,
        name="run_dbt:show model.example",
        source="dbt",
        status=status,
        mandatory=True,
        exit_code=0 if status == "passed" else 2,
        duration_ms=10,
        created_at=created_at,
    )


def test_validation_report_uses_the_latest_attempt_for_each_check() -> None:
    first_attempt = datetime(2026, 10, 5, tzinfo=UTC)
    report = ValidationReportAssembler(
        _Evidence([
            _receipt(receipt_id="failed-first", status="failed", created_at=first_attempt),
            _receipt(
                receipt_id="passed-second",
                status="passed",
                created_at=first_attempt + timedelta(seconds=1),
            ),
        ])
    ).assemble(
        candidate=_candidate(),
        qa_verdict=QaVerdict(
            id="qa-verdict",
            qa_agent_id="qa",
            task_id="qa-task",
            candidate_digest="a" * 64,
            status="passed",
            created_at=first_attempt + timedelta(seconds=2),
        ),
    )

    tool_checks = [check for check in report.checks if check.name != "independent_qa"]
    assert len(tool_checks) == 1
    assert tool_checks[0].receipt_id == "passed-second"
    assert tool_checks[0].status == "passed"
