"""Offline LLM quality metrics and release gate checks."""

from __future__ import annotations

from math import ceil
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GateMetricName = Literal[
    "answer_correctness",
    "sql_correctness",
    "execution_success_rate",
    "grounded_claim_ratio",
    "tool_efficiency",
    "latency_p95",
    "token_cost",
]


class EvalSampleResult(BaseModel):
    """Single scored sample from eval dataset."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    tier: Literal["smoke", "core", "stress"]
    answer_correct: bool
    sql_correct: bool
    execution_succeeded: bool
    grounded_claim_ratio: float = Field(ge=0, le=1)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    token_cost: float = Field(ge=0)


class QualityGatePolicy(BaseModel):
    """Threshold policy used by release gates."""

    model_config = ConfigDict(extra="forbid")

    min_metrics: dict[GateMetricName, float] = Field(default_factory=dict)
    max_metrics: dict[GateMetricName, float] = Field(default_factory=dict)


class QualityGateReport(BaseModel):
    """Computed quality metrics and gate result."""

    metrics: dict[GateMetricName, float]
    passed: bool
    violations: list[str] = Field(default_factory=list)


def compute_metrics(samples: list[EvalSampleResult]) -> dict[GateMetricName, float]:
    """Compute normalized quality metrics from scored samples."""
    if not samples:
        return {
            "answer_correctness": 0.0,
            "sql_correctness": 0.0,
            "execution_success_rate": 0.0,
            "grounded_claim_ratio": 0.0,
            "tool_efficiency": 0.0,
            "latency_p95": 0.0,
            "token_cost": 0.0,
        }

    successful_answers = [sample for sample in samples if sample.answer_correct]
    latencies = sorted(sample.latency_ms for sample in samples)
    p95_index = max(0, ceil(len(latencies) * 0.95) - 1)

    if successful_answers:
        tool_efficiency = mean(
            sample.tool_calls
            for sample in successful_answers
        )
    else:
        tool_efficiency = mean(sample.tool_calls for sample in samples)

    return {
        "answer_correctness": mean(
            1.0 if sample.answer_correct else 0.0
            for sample in samples
        ),
        "sql_correctness": mean(
            1.0 if sample.sql_correct else 0.0
            for sample in samples
        ),
        "execution_success_rate": mean(
            1.0 if sample.execution_succeeded else 0.0
            for sample in samples
        ),
        "grounded_claim_ratio": mean(
            sample.grounded_claim_ratio
            for sample in samples
        ),
        "tool_efficiency": tool_efficiency,
        "latency_p95": float(latencies[p95_index]),
        "token_cost": mean(sample.token_cost for sample in samples),
    }


def evaluate_quality_gates(
    *,
    samples: list[EvalSampleResult],
    policy: QualityGatePolicy,
) -> QualityGateReport:
    """Evaluate quality gate thresholds against measured metrics."""
    metrics = compute_metrics(samples)
    violations: list[str] = []

    for metric_name, threshold in policy.min_metrics.items():
        value = metrics[metric_name]
        if value < threshold:
            violations.append(
                f"{metric_name}={value:.4f} is below min threshold {threshold:.4f}"
            )

    for metric_name, threshold in policy.max_metrics.items():
        value = metrics[metric_name]
        if value > threshold:
            violations.append(
                f"{metric_name}={value:.4f} exceeds max threshold {threshold:.4f}"
            )

    return QualityGateReport(
        metrics=metrics,
        passed=len(violations) == 0,
        violations=violations,
    )

