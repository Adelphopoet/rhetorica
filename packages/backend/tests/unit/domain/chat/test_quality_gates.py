"""Tests for LLM quality gate scoring."""

from rhetorica_backend.domain.chat.eval.quality_gates import (
    EvalSampleResult,
    QualityGatePolicy,
    compute_metrics,
    evaluate_quality_gates,
)


def test_compute_metrics_returns_expected_shape() -> None:
    samples = [
        EvalSampleResult(
            case_id="smoke-1",
            tier="smoke",
            answer_correct=True,
            sql_correct=True,
            execution_succeeded=True,
            grounded_claim_ratio=1.0,
            tool_calls=2,
            latency_ms=1000,
            token_cost=0.2,
        ),
        EvalSampleResult(
            case_id="core-1",
            tier="core",
            answer_correct=False,
            sql_correct=True,
            execution_succeeded=False,
            grounded_claim_ratio=0.5,
            tool_calls=4,
            latency_ms=3000,
            token_cost=0.4,
        ),
    ]

    metrics = compute_metrics(samples)

    assert metrics["answer_correctness"] == 0.5
    assert metrics["sql_correctness"] == 1.0
    assert metrics["execution_success_rate"] == 0.5
    assert metrics["grounded_claim_ratio"] == 0.75
    assert metrics["latency_p95"] == 3000.0


def test_evaluate_quality_gates_fails_when_threshold_is_violated() -> None:
    samples = [
        EvalSampleResult(
            case_id="smoke-1",
            tier="smoke",
            answer_correct=False,
            sql_correct=False,
            execution_succeeded=False,
            grounded_claim_ratio=0.1,
            tool_calls=5,
            latency_ms=15_000,
            token_cost=3.0,
        )
    ]
    policy = QualityGatePolicy(
        min_metrics={
            "answer_correctness": 0.7,
            "sql_correctness": 0.7,
            "execution_success_rate": 0.7,
            "grounded_claim_ratio": 0.7,
        },
        max_metrics={
            "tool_efficiency": 3.0,
            "latency_p95": 9000,
            "token_cost": 1.0,
        },
    )

    report = evaluate_quality_gates(samples=samples, policy=policy)

    assert report.passed is False
    assert len(report.violations) >= 1
