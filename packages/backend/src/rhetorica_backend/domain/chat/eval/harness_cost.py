"""Offline, explicit-price estimates from reported model-call usage."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class TokenPrices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(min_length=1)
    input_per_1000: Decimal = Field(ge=0, allow_inf_nan=False)
    cached_per_1000: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_1000: Decimal = Field(ge=0, allow_inf_nan=False)
    builtin_tool_per_1000: Decimal = Field(ge=0, allow_inf_nan=False)


class _Usage(BaseModel):
    prompt_tokens: int = Field(ge=0, strict=True)
    cached_input_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)
    # These are a disjoint part of input, not an extra copy of tool-result text.
    # SDK FunctionTools/MCP have no separately billed hosted-tool tokens.
    billable_builtin_tool_tokens: int = Field(default=0, ge=0, strict=True)
    usage_reported: bool
    cache_usage_reported: bool

    @model_validator(mode="after")
    def check_input_partition(self) -> "_Usage":
        if self.cached_input_tokens + self.billable_builtin_tool_tokens > self.prompt_tokens:
            raise ValueError("Cached and builtin-tool input must be disjoint subsets of input")
        if not self.usage_reported or not self.cache_usage_reported:
            raise ValueError("Usage and cache counters must be explicitly reported")
        return self


class CostEstimate(BaseModel):
    currency: str
    total: Decimal | None
    known_subtotal: Decimal
    model_calls: int
    missing_or_invalid_calls: list[int]


def estimate_cost(calls: list[dict[str, Any]], prices: TokenPrices) -> CostEstimate:
    """No inference from call count, prompt length, provider name or missing usage."""
    subtotal = Decimal(0)
    missing: list[int] = []
    for index, call in enumerate(calls, 1):
        try:
            usage = _Usage.model_validate(call)
        except ValidationError:
            missing.append(index)
            continue
        ordinary = (
            usage.prompt_tokens - usage.cached_input_tokens - usage.billable_builtin_tool_tokens
        )
        subtotal += (
            ordinary * prices.input_per_1000
            + usage.cached_input_tokens * prices.cached_per_1000
            + usage.billable_builtin_tool_tokens * prices.builtin_tool_per_1000
            + usage.completion_tokens * prices.output_per_1000
        ) / 1000
    return CostEstimate(
        currency=prices.currency,
        total=subtotal if calls and not missing else None,
        known_subtotal=subtotal,
        model_calls=len(calls),
        missing_or_invalid_calls=missing,
    )


def cost_report(
    rows: list[dict[str, Any]], prices: dict[str, TokenPrices]
) -> dict[str, Any]:
    """Descriptive report only: price estimates cannot certify answer quality."""
    providers: dict[str, Any] = {}
    for provider in sorted({row["measurement"]["provider"] for row in rows}):
        selected = [row for row in rows if row["measurement"]["provider"] == provider]
        if provider not in prices:
            providers[provider] = {"error": "explicit_provider_prices_missing", "total": None}
            continue
        estimates = [estimate_cost(row.get("model_calls", []), prices[provider]) for row in selected]
        complete = all(item.total is not None for item in estimates)
        providers[provider] = {
            "prices": prices[provider].model_dump(mode="json"),
            "total": str(sum((item.total or Decimal(0) for item in estimates), Decimal(0)))
            if complete else None,
            "known_subtotal": str(sum((item.known_subtotal for item in estimates), Decimal(0))),
            "dialogues": [
                {
                    "case_id": row["measurement"]["case_id"],
                    "repeat": row["measurement"]["repeat"],
                    "completed": row["measurement"]["completed"],
                    **estimate.model_dump(mode="json"),
                }
                for row, estimate in zip(selected, estimates, strict=True)
            ],
        }
    return {"purpose": "descriptive_cost_estimate_not_acceptance", "providers": providers}
