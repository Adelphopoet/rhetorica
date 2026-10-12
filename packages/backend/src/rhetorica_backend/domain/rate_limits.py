from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


class RateLimitStoreError(RuntimeError):
    """Raised when the shared rate-limit state cannot be read or updated."""


class RateLimitUnavailable(RuntimeError):
    """Raised when a configured hard limit cannot be enforced."""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


class SlidingWindowStore(Protocol):
    async def consume(
        self,
        *,
        workspace_id: str,
        user_id: str,
        limit: int,
        window_seconds: int,
        request_id: str,
    ) -> RateLimitDecision: ...

    async def reset(self, *, workspace_id: str, user_id: str) -> None: ...


class UserApiRateLimitService:
    """Apply one exact rolling-window quota per stable user identity."""

    def __init__(self, store: SlidingWindowStore, *, window_seconds: int = 3600) -> None:
        if window_seconds <= 0:
            raise ValueError("rate_limit_window_must_be_positive")
        self._store = store
        self._window_seconds = window_seconds

    async def check(
        self,
        *,
        workspace_id: str,
        user_id: str,
        requests_per_hour: int | None,
    ) -> RateLimitDecision | None:
        if requests_per_hour is None:
            return None
        if requests_per_hour <= 0:
            raise ValueError("api_rate_limit_must_be_positive")
        try:
            return await self._store.consume(
                workspace_id=workspace_id,
                user_id=user_id,
                limit=requests_per_hour,
                window_seconds=self._window_seconds,
                request_id=str(uuid4()),
            )
        except RateLimitStoreError as exc:
            raise RateLimitUnavailable("rate_limit_unavailable") from exc

    async def reset(self, *, workspace_id: str, user_id: str) -> None:
        try:
            await self._store.reset(workspace_id=workspace_id, user_id=user_id)
        except RateLimitStoreError as exc:
            raise RateLimitUnavailable("rate_limit_unavailable") from exc
