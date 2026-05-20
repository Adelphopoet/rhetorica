#!/usr/bin/env python3
"""Run offline LLM quality gate checks from scored samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rhetorica_backend.domain.chat.eval.quality_gates import (
    EvalSampleResult,
    QualityGatePolicy,
    evaluate_quality_gates,
)


def _load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM quality release gates.")
    parser.add_argument(
        "--samples",
        required=True,
        help="Path to JSON array with scored eval samples.",
    )
    parser.add_argument(
        "--policy",
        required=True,
        help="Path to gate policy JSON.",
    )
    args = parser.parse_args()

    samples_payload = _load_json(Path(args.samples))
    if not isinstance(samples_payload, list):
        parser.error("--samples must contain JSON array")

    policy_payload = _load_json(Path(args.policy))
    if not isinstance(policy_payload, dict):
        parser.error("--policy must contain JSON object")

    samples = [EvalSampleResult.model_validate(item) for item in samples_payload]
    policy = QualityGatePolicy.model_validate(policy_payload)
    report = evaluate_quality_gates(samples=samples, policy=policy)

    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
