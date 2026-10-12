from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from rhetorica_backend.domain.rate_limits import RateLimitDecision, RateLimitStoreError

SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local request_id = ARGV[3]

local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local cutoff_ms = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff_ms)
local count = redis.call('ZCARD', key)

if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local reset_ms = window_ms
  if oldest[2] then
    reset_ms = math.max(1, math.ceil(tonumber(oldest[2]) + window_ms - now_ms))
  end
  return {0, 0, math.ceil(reset_ms / 1000)}
end

redis.call('ZADD', key, now_ms, request_id)
redis.call('PEXPIRE', key, window_ms + 1000)
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset_ms = window_ms
if oldest[2] then
  reset_ms = math.max(1, math.ceil(tonumber(oldest[2]) + window_ms - now_ms))
end
return {1, limit - count - 1, math.ceil(reset_ms / 1000)}
"""


class RedisSlidingWindowStore:
    """Redis-backed exact sliding-window counter shared by all API replicas."""

    def __init__(self, client: Redis, *, key_prefix: str = "rhetorica:rate-limit:api") -> None:
        self._client = client
        self._key_prefix = key_prefix.rstrip(":")

    def key(self, *, workspace_id: str, user_id: str) -> str:
        return f"{self._key_prefix}:{{{workspace_id}:{user_id}}}"

    async def consume(
        self,
        *,
        workspace_id: str,
        user_id: str,
        limit: int,
        window_seconds: int,
        request_id: str,
    ) -> RateLimitDecision:
        try:
            result: Any = await self._client.eval(
                SLIDING_WINDOW_SCRIPT,
                1,
                self.key(workspace_id=workspace_id, user_id=user_id),
                limit,
                window_seconds * 1000,
                request_id,
            )
        except RedisError as exc:
            raise RateLimitStoreError("redis_rate_limit_consume_failed") from exc

        allowed, remaining, reset_after_seconds = (int(value) for value in result)
        return RateLimitDecision(
            allowed=bool(allowed),
            limit=limit,
            remaining=max(0, remaining),
            reset_after_seconds=max(1, reset_after_seconds),
        )

    async def reset(self, *, workspace_id: str, user_id: str) -> None:
        try:
            await self._client.delete(self.key(workspace_id=workspace_id, user_id=user_id))
        except RedisError as exc:
            raise RateLimitStoreError("redis_rate_limit_reset_failed") from exc
