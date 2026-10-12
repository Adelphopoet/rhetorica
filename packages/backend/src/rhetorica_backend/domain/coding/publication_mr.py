"""Commit trailers and Draft MR text built from persisted QA evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping

from rhetorica_backend.contracts.coding import CandidateManifest, ValidationReport, ValidationStatus

MAX_MR_CELL_CHARS = 300
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

TRAILER_RUN = "Rhetorica-Run"
TRAILER_CANDIDATE = "Rhetorica-Candidate"
TRAILER_REPORT = "Rhetorica-Report"
TRAILER_BASE = "Rhetorica-Base"


def publication_branch_name(run_id: str) -> str:
    return f"feature/rhetorica/{run_id}"


def commit_message(
    *,
    run_id: str,
    candidate_digest: str,
    report_digest: str,
    base_sha: str,
) -> str:
    return (
        f"Rhetorica coding publication for run {run_id}\n\n"
        f"{TRAILER_RUN}: {run_id}\n"
        f"{TRAILER_CANDIDATE}: {candidate_digest}\n"
        f"{TRAILER_REPORT}: {report_digest}\n"
        f"{TRAILER_BASE}: {base_sha}\n"
    )


def parse_operation_trailers(message: str | None) -> dict[str, str] | None:
    if not message:
        return None
    found: dict[str, str] = {}
    for line in message.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {TRAILER_RUN, TRAILER_CANDIDATE, TRAILER_REPORT, TRAILER_BASE}:
            found[key] = value.strip()
    expected = {TRAILER_RUN, TRAILER_CANDIDATE, TRAILER_REPORT, TRAILER_BASE}
    if set(found) != expected:
        return None
    return found


def trailers_match(
    message: str | None,
    *,
    run_id: str,
    candidate_digest: str,
    report_digest: str,
    base_sha: str,
) -> bool:
    parsed = parse_operation_trailers(message)
    if parsed is None:
        return False
    return parsed == {
        TRAILER_RUN: run_id,
        TRAILER_CANDIDATE: candidate_digest,
        TRAILER_REPORT: report_digest,
        TRAILER_BASE: base_sha,
    }


def draft_mr_title(run_id: str) -> str:
    return f"Draft: Rhetorica coding run {run_id}"


def draft_mr_description(
    *,
    run_id: str,
    candidate: CandidateManifest,
    report: ValidationReport,
    commit_sha: str,
    extra: Mapping[str, str] | None = None,
) -> str:
    rows = [
        "| Check | Status | Mandatory | Reason |",
        "|---|---|---|---|",
    ]
    limitations: list[str] = []
    for check in report.checks:
        name = _inline_code(check.name)
        reason = _inline_code(check.reason or "")
        rows.append(
            f"| {name} | {_inline_code(str(check.status))} | "
            f"{'yes' if check.mandatory else 'no'} | {reason} |"
        )
        if check.status == ValidationStatus.NOT_RUN:
            limitations.append(f"- {name} was `not_run`" + (f": {reason}" if reason else ""))
    extra_lines = [
        f"- {_inline_code(key)}: {_inline_code(value)}" for key, value in (extra or {}).items()
    ]
    limitation_block = (
        "## Limitations\n\n" + "\n".join(limitations) + "\n"
        if limitations
        else "## Limitations\n\n- None recorded.\n"
    )
    extras = ("\n".join(extra_lines) + "\n") if extra_lines else ""
    return (
        f"## Rhetorica coding publication\n\n"
        f"- Run: {_inline_code(run_id)}\n"
        f"- Project candidate digest: {_inline_code(candidate.digest)}\n"
        f"- Validation report digest: {_inline_code(report.report_digest)}\n"
        f"- Base SHA: {_inline_code(candidate.base_sha)}\n"
        f"- Commit SHA: {_inline_code(commit_sha)}\n"
        f"{extras}\n"
        f"## QA checks\n\n"
        f"{chr(10).join(rows)}\n\n"
        f"{limitation_block}\n"
        "Raw DWH result exports are not attached.\n"
    )


def _inline_code(value: str, *, limit: int = MAX_MR_CELL_CHARS) -> str:
    # One line inside a code span: GitLab runs quick actions only at line start and
    # skips mentions/references inside code, so report text cannot act on the MR.
    text = " ".join(_CONTROL_CHARS.sub(" ", value).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    text = text.replace("`", "'").replace("|", "\\|")
    return f"`{text}`" if text else ""
