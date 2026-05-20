from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from rhetorica_backend.infra.logging import (
    bind_context,
    clear_context,
    emit_stdout_event,
    get_logger,
)

logger = get_logger("rhetorica.api.http")


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        client_host = request.client.host if request.client is not None else None
        query_params = dict(request.query_params)
        clear_context()
        bind_context(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        request_logger = logger.bind(
            query_params=query_params,
            client_host=client_host,
        )

        started_at = perf_counter()
        request_logger.info("http.request.started")
        emit_stdout_event(
            "http.request.started",
            query_params=query_params,
            client_host=client_host,
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            request_logger.exception("http.request.failed", duration_ms=duration_ms)
            emit_stdout_event(
                "http.request.failed",
                duration_ms=duration_ms,
                request_id=request_id,
                http_method=request.method,
                http_path=request.url.path,
            )
            clear_context()
            raise

        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        request_logger.info(
            "http.request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        emit_stdout_event(
            "http.request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        clear_context()
        return response
