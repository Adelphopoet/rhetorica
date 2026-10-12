from __future__ import annotations

from collections import defaultdict

import pytest
from rhetorica_backend.domain.rate_limits import (
    RateLimitDecision,
    RateLimitStoreError,
    RateLimitUnavailable,
    UserApiRateLimitService,
)


class InMemorySlidingWindowStore:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = defaultdict(int)
        self.consume_calls = 0

    async def consume(
        self,
        *,
        workspace_id: str,
        user_id: str,
        limit: int,
        window_seconds: int,
        request_id: str,
    ) -> RateLimitDecision:
        del request_id
        assert window_seconds == 3600
        self.consume_calls += 1
        key = (workspace_id, user_id)
        count = self.counts[key]
        if count >= limit:
            return RateLimitDecision(False, limit, 0, 300)
        self.counts[key] += 1
        return RateLimitDecision(True, limit, limit - self.counts[key], 3600)

    async def reset(self, *, workspace_id: str, user_id: str) -> None:
        self.counts.pop((workspace_id, user_id), None)


class FailingSlidingWindowStore(InMemorySlidingWindowStore):
    async def consume(self, **kwargs) -> RateLimitDecision:
        del kwargs
        raise RateLimitStoreError("redis_down")

    async def reset(self, *, workspace_id: str, user_id: str) -> None:
        del workspace_id, user_id
        raise RateLimitStoreError("redis_down")


@pytest.mark.asyncio
async def test_unconfigured_user_skips_shared_store() -> None:
    store = InMemorySlidingWindowStore()
    service = UserApiRateLimitService(store)

    decision = await service.check(
        workspace_id="default",
        user_id="unlimited",
        requests_per_hour=None,
    )

    assert decision is None
    assert store.consume_calls == 0


@pytest.mark.asyncio
async def test_limit_allows_capacity_then_rejects_same_user() -> None:
    store = InMemorySlidingWindowStore()
    service = UserApiRateLimitService(store)

    decisions = [
        await service.check(
            workspace_id="default",
            user_id="limited-user",
            requests_per_hour=2,
        )
        for _ in range(3)
    ]

    assert [decision.allowed for decision in decisions if decision is not None] == [
        True,
        True,
        False,
    ]
    assert decisions[1] == RateLimitDecision(True, 2, 0, 3600)
    assert decisions[2] == RateLimitDecision(False, 2, 0, 300)


@pytest.mark.asyncio
async def test_usage_isolated_by_workspace_and_user_then_reset() -> None:
    store = InMemorySlidingWindowStore()
    service = UserApiRateLimitService(store)

    for user_id in ("user-a", "user-b"):
        decision = await service.check(
            workspace_id="default",
            user_id=user_id,
            requests_per_hour=1,
        )
        assert decision is not None and decision.allowed

    blocked = await service.check(
        workspace_id="default",
        user_id="user-a",
        requests_per_hour=1,
    )
    assert blocked is not None and not blocked.allowed

    await service.reset(workspace_id="default", user_id="user-a")
    allowed = await service.check(
        workspace_id="default",
        user_id="user-a",
        requests_per_hour=1,
    )
    assert allowed is not None and allowed.allowed


@pytest.mark.asyncio
async def test_store_failure_becomes_hard_limit_unavailable() -> None:
    service = UserApiRateLimitService(FailingSlidingWindowStore())

    with pytest.raises(RateLimitUnavailable, match="rate_limit_unavailable"):
        await service.check(
            workspace_id="default",
            user_id="limited-user",
            requests_per_hour=20,
        )

    with pytest.raises(RateLimitUnavailable, match="rate_limit_unavailable"):
        await service.reset(workspace_id="default", user_id="limited-user")
