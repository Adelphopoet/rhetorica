from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from rhetorica_backend.domain.rate_limits import RateLimitStoreError
from rhetorica_backend.infra.redis_rate_limits import RedisSlidingWindowStore


class FakeRedis:
    def __init__(self, result=(1, 19, 3600), *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.eval_args = None
        self.deleted_key = None

    async def eval(self, *args):
        if self.fail:
            raise RedisConnectionError("down")
        self.eval_args = args
        return self.result

    async def delete(self, key):
        if self.fail:
            raise RedisConnectionError("down")
        self.deleted_key = key


@pytest.mark.asyncio
async def test_redis_store_returns_atomic_script_decision_and_cluster_safe_key() -> None:
    redis = FakeRedis()
    store = RedisSlidingWindowStore(redis, key_prefix="test:rate-limit")  # type: ignore[arg-type]

    decision = await store.consume(
        workspace_id="workspace-a",
        user_id="user-1",
        limit=20,
        window_seconds=3600,
        request_id="request-1",
    )

    assert decision.allowed is True
    assert decision.remaining == 19
    assert decision.reset_after_seconds == 3600
    assert redis.eval_args is not None
    assert redis.eval_args[2] == "test:rate-limit:{workspace-a:user-1}"


@pytest.mark.asyncio
async def test_redis_store_reset_and_failures() -> None:
    redis = FakeRedis()
    store = RedisSlidingWindowStore(redis)  # type: ignore[arg-type]

    await store.reset(workspace_id="default", user_id="user-1")
    assert redis.deleted_key == "rhetorica:rate-limit:api:{default:user-1}"

    failing = RedisSlidingWindowStore(FakeRedis(fail=True))  # type: ignore[arg-type]
    with pytest.raises(RateLimitStoreError):
        await failing.consume(
            workspace_id="default",
            user_id="user-1",
            limit=20,
            window_seconds=3600,
            request_id="request-1",
        )
    with pytest.raises(RateLimitStoreError):
        await failing.reset(workspace_id="default", user_id="user-1")
