from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from rhetorica_backend.domain.rate_limits import (
    RateLimitDecision,
    RateLimitUnavailable,
    UserApiRateLimitService,
)
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.redis_rate_limits import RedisSlidingWindowStore

from rhetorica_api.app.auth import AuthContext
from rhetorica_api.app.settings import settings

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        health_check_interval=30,
    )


@lru_cache(maxsize=1)
def api_rate_limit_service() -> UserApiRateLimitService:
    return UserApiRateLimitService(
        RedisSlidingWindowStore(
            _redis_client(),
            key_prefix=settings.rate_limit_redis_key_prefix,
        )
    )


async def close_api_rate_limit_runtime() -> None:
    if _redis_client.cache_info().currsize:
        await _redis_client().aclose()
    api_rate_limit_service.cache_clear()
    _redis_client.cache_clear()


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(decision.limit),
        "RateLimit-Remaining": str(decision.remaining),
        "RateLimit-Reset": str(decision.reset_after_seconds),
    }


def _is_counted_request(request: Request) -> bool:
    return request.method != "OPTIONS" and request.url.path.startswith("/v1/")


def _is_self_rate_limit_recovery_request(
    request: Request,
    auth_context: AuthContext,
) -> bool:
    request_path = request.url.path.rstrip("/")
    rate_limit_path = f"/v1/admin/users/{auth_context.user_id}/rate-limit"
    return (
        request.method == "DELETE" and request_path == rate_limit_path
    ) or (
        request.method == "POST" and request_path == f"{rate_limit_path}/reset"
    )


def install_api_rate_limits(app: FastAPI) -> None:
    @app.middleware("http")
    async def api_rate_limit_middleware(request: Request, call_next) -> Response:
        if not _is_counted_request(request):
            return await call_next(request)

        auth_context: AuthContext | None = getattr(request.state, "auth_context", None)
        if auth_context is None:
            return await call_next(request)
        if _is_self_rate_limit_recovery_request(request, auth_context):
            return await call_next(request)

        try:
            decision = await api_rate_limit_service().check(
                workspace_id=auth_context.workspace_id,
                user_id=auth_context.user_id,
                requests_per_hour=auth_context.user.api_rate_limit_per_hour,
            )
        except RateLimitUnavailable:
            logger.exception(
                "api_rate_limit.unavailable",
                workspace_id=auth_context.workspace_id,
                user_id=auth_context.user_id,
            )
            return JSONResponse(
                {"detail": "rate_limit_unavailable"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if decision is None:
            return await call_next(request)

        headers = rate_limit_headers(decision)
        if not decision.allowed:
            logger.info(
                "api_rate_limit.exceeded",
                workspace_id=auth_context.workspace_id,
                user_id=auth_context.user_id,
                limit=decision.limit,
                retry_after_seconds=decision.reset_after_seconds,
            )
            return JSONResponse(
                {"detail": "api_rate_limit_exceeded"},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={**headers, "Retry-After": str(decision.reset_after_seconds)},
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response
