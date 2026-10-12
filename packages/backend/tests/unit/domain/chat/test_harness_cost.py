"""Offline billing uses reported inclusive usage, never prompt-size guesses."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from rhetorica_backend.domain.chat.eval.harness_cost import TokenPrices, cost_report, estimate_cost


@pytest.fixture
def prices():
    return TokenPrices(
        currency="RUB", input_per_1000="0.3", cached_per_1000="0.075",
        output_per_1000="0.5", builtin_tool_per_1000="0.075",
    )


def call(**overrides):
    return {
        "prompt_tokens": 1000, "cached_input_tokens": 700, "completion_tokens": 100,
        "usage_reported": True, "cache_usage_reported": True, **overrides,
    }


def test_disjoint_input_price_and_auxiliary_calls(prices):
    # Local FunctionTool/MCP results remain ordinary input; call phase does not
    # exclude auxiliary compaction or a separately reported retry from cost.
    result = estimate_cost([
        call(phase="primary"),
        call(phase="compaction", cached_input_tokens=0),
        call(phase="retry", cached_input_tokens=0, billable_builtin_tool_tokens=200),
    ], prices)
    assert result.total == Decimal("0.8475")
    assert result.known_subtotal == result.total
    assert result.model_calls == 3
    assert result.missing_or_invalid_calls == []


@pytest.mark.parametrize("invalid", [
    {"cache_usage_reported": False}, {"usage_reported": False},
    {"cached_input_tokens": None}, {"prompt_tokens": -1},
    {"cached_input_tokens": 1001}, {"completion_tokens": 1.5},
    {"billable_builtin_tool_tokens": 301},
])
def test_missing_or_inconsistent_usage_is_unknown_not_free(prices, invalid):
    result = estimate_cost([call(), call(**invalid)], prices)
    assert result.total is None
    assert result.known_subtotal == Decimal("0.1925")
    assert result.missing_or_invalid_calls == [2]


def test_explicit_zero_and_absent_calls_are_distinct(prices):
    assert estimate_cost([call(cached_input_tokens=0)], prices).total == Decimal("0.35")
    assert estimate_cost([call(
        prompt_tokens=0, cached_input_tokens=0, completion_tokens=0,
    )], prices).total == 0
    assert estimate_cost([], prices).total is None


def test_prices_are_explicit_finite_nonnegative():
    for invalid in ("-1", "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            TokenPrices(
                currency="RUB", input_per_1000=invalid, cached_per_1000="0",
                output_per_1000="0", builtin_tool_per_1000="0",
            )


def test_cost_report_keeps_failures_and_never_invents_other_provider_prices(prices):
    rows = [
        {"measurement": {"provider": provider, "case_id": "case", "repeat": 1,
                         "completed": False}, "model_calls": [call()]}
        for provider in ("deepseek", "another")
    ]
    report = cost_report(rows, {"deepseek": prices})
    assert report["providers"]["deepseek"]["total"] == "0.1925"
    assert report["providers"]["deepseek"]["dialogues"][0]["completed"] is False
    assert report["providers"]["another"]["total"] is None
    rows[0]["model_calls"] = []
    assert cost_report(rows, {"deepseek": prices})["providers"]["deepseek"]["total"] is None
