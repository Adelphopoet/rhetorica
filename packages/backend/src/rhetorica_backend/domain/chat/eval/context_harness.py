"""Comparable harness gates. Missing measurements never count as a pass."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class HarnessMeasurement(BaseModel):
    case_id: str
    provider: str
    mode: Literal["baseline", "direct", "dispatch"]
    repeat: int = Field(ge=1, le=3)
    fixture_hash: str
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    cache_reported: bool
    latency_ms: float = Field(gt=0)
    answer_correct: bool | None = None
    sql_correct: bool | None = None
    grounded: bool | None = None
    completed: bool


@dataclass(frozen=True)
class HarnessGateResult:
    passed: bool
    failures: tuple[str, ...]
    cache_ratio: float | None
    uncached_tokens: int
    p95_latency_ms: float | None


def evaluate_harness(
    records: list[HarnessMeasurement],
    *,
    baseline: list[HarnessMeasurement],
    case_ids: set[str],
    provider: str,
) -> HarnessGateResult:
    """Evaluate one provider/mode against matching external-result fixtures."""
    failures: list[str] = []
    expected = {(case, repeat) for case in case_ids for repeat in (1, 2, 3)}

    def keyed(
        rows: list[HarnessMeasurement], label: str
    ) -> dict[tuple[str, int], HarnessMeasurement]:
        keys = {(r.case_id, r.repeat): r for r in rows}
        if len(rows) != len(keys) or set(keys) != expected or len(case_ids) != 30:
            failures.append(f"{label}_matrix_incomplete_or_duplicated")
        if any(r.provider != provider for r in rows):
            failures.append(f"{label}_provider_mismatch")
        if not rows or any(
            not r.completed or r.input_tokens == 0 or not r.cache_reported for r in rows
        ):
            failures.append(f"{label}_measurement_missing_or_failed")
        if any(r.cached_input_tokens > r.input_tokens for r in rows):
            failures.append(f"{label}_invalid_cache_accounting")
        if any(None in (r.answer_correct, r.sql_correct, r.grounded) for r in rows):
            failures.append(f"{label}_quality_unrated")
        return keys

    current = keyed(records, "candidate")
    reference = keyed(baseline, "baseline")
    if len({r.mode for r in records}) != 1 or any(r.mode != "baseline" for r in baseline):
        failures.append("mixed_modes")
    for key in current.keys() & reference.keys():
        row, ref = current[key], reference[key]
        if not row.fixture_hash or row.fixture_hash != ref.fixture_hash:
            failures.append("external_results_not_comparable")
        for criterion in ("answer_correct", "sql_correct", "grounded"):
            if getattr(ref, criterion) is True and getattr(row, criterion) is not True:
                failures.append(f"{criterion}_regressed")

    def ratio(rows: list[HarnessMeasurement]) -> float | None:
        total = sum(r.input_tokens for r in rows)
        return (
            sum(r.cached_input_tokens for r in rows) / total
            if total and all(r.cache_reported for r in rows)
            else None
        )

    def p95(rows: list[HarnessMeasurement]) -> float | None:
        ordered = sorted(r.latency_ms for r in rows)
        return ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None

    cache, baseline_cache = ratio(records), ratio(baseline)
    if cache is None or baseline_cache is None or cache < max(0.7, baseline_cache):
        failures.append("cache_ratio_gate")
    uncached = sum(r.input_tokens - r.cached_input_tokens for r in records)
    if uncached >= sum(r.input_tokens - r.cached_input_tokens for r in baseline):
        failures.append("uncached_tokens_not_reduced")
    latency, baseline_latency = p95(records), p95(baseline)
    if latency is None or baseline_latency is None or latency > baseline_latency:
        failures.append("p95_latency_regressed")
    return HarnessGateResult(not failures, tuple(sorted(set(failures))), cache, uncached, latency)
