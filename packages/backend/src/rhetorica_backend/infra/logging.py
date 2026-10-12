from __future__ import annotations

import hashlib
import logging
import logging.config
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import orjson
import structlog
from opentelemetry import trace
from pydantic import BaseModel

_LOGGING_CONFIGURED = False
_STATIC_CONTEXT: dict[str, Any] = {}
_REDACTED = "[REDACTED]"
_TRUNCATED = "…"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "set-cookie",
    "token",
    "x-api-key",
}
_FILE_TOOL_NAMES = frozenset({
    "list_files",
    "inspect_file",
    "run_file_code",
    "publish_files",
    "export_sql_result",
})
EventLogger = structlog.stdlib.BoundLogger


def is_dev_environment(environment: str | None) -> bool:
    normalized = (environment or "").strip().lower()
    return normalized in {"dev", "development", "local", "test"}


def configure_logging(
    *,
    service_name: str,
    environment: str,
    log_level: str | None = None,
    json_logs: bool | None = None,
) -> None:
    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED:
        return

    dev_mode = is_dev_environment(environment)
    level = (log_level or ("DEBUG" if dev_mode else "INFO")).upper()
    render_as_json = json_logs if json_logs is not None else not sys.stderr.isatty()

    _STATIC_CONTEXT.clear()
    _STATIC_CONTEXT.update({
        "service": service_name,
        "environment": environment,
    })

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_static_context,
        _add_trace_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        _sanitize_event_dict,
    ]
    if dev_mode:
        shared_processors.append(
            structlog.processors.CallsiteParameterAdder({
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            })
        )

    renderer_processors: list[Any] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if render_as_json:
        renderer_processors.extend([
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(serializer=_json_serializer),
        ])
    else:
        renderer_processors.extend([
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=True),
        ])

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": structlog.stdlib.ProcessorFormatter,
                "foreign_pre_chain": shared_processors,
                "processors": renderer_processors,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "": {"handlers": ["default"], "level": level},
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": level,
                "propagate": False,
            },
            "celery": {"handlers": ["default"], "level": level, "propagate": False},
            "sqlalchemy": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.engine": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.pool": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.orm": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    })

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOGGING_CONFIGURED = True


def get_logger(name: str | None = None, **fields: Any) -> EventLogger:
    logger = (
        structlog.stdlib.get_logger(name) if name is not None else structlog.stdlib.get_logger()
    )
    return bind_logger(logger, **fields)


def bind_logger(logger: EventLogger, **fields: Any) -> EventLogger:
    clean = {key: value for key, value in fields.items() if value is not None}
    return logger.bind(**clean) if clean else logger


def bind_context(**kwargs: Any) -> None:
    clean = {key: value for key, value in kwargs.items() if value is not None}
    if clean:
        structlog.contextvars.bind_contextvars(**clean)


def unbind_context(*keys: str) -> None:
    if keys:
        structlog.contextvars.unbind_contextvars(*keys)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def emit_stdout_event(event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        **_STATIC_CONTEXT,
        **sanitize_for_logging(fields),
        **_trace_context_fields(),
        "timestamp": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    }
    sys.stdout.write(_json_serializer(payload) + "\n")
    sys.stdout.flush()


def sanitize_for_logging(
    value: Any,
    *,
    max_string_length: int = 8000,
    max_collection_items: int = 100,
    max_depth: int = 8,
) -> Any:
    return _sanitize_value(
        value,
        max_string_length=max_string_length,
        max_collection_items=max_collection_items,
        max_depth=max_depth,
        depth=0,
        field_name=None,
    )


def sanitize_tool_arguments_for_logging(
    tool_name: str | None,
    arguments: Mapping[str, Any] | None,
) -> Any:
    """Never emit executable code while retaining bounded tool diagnostics."""
    if not isinstance(arguments, Mapping):
        return sanitize_for_logging(arguments)
    if tool_name not in _FILE_TOOL_NAMES:
        return sanitize_for_logging(arguments)
    safe_arguments = dict(arguments)
    code = safe_arguments.pop("code", None)
    if isinstance(code, str):
        safe_arguments["code_chars"] = len(code)
    return sanitize_for_logging(safe_arguments)


def llm_payload_contains_file_tools(payload: Mapping[str, Any]) -> bool:
    """Detect payloads whose request or response may contain private file data."""
    tools = payload.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, str | bytes):
        return False
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function")
        name = function.get("name") if isinstance(function, Mapping) else item.get("name")
        if name in _FILE_TOOL_NAMES:
            return True
    return False


def llm_content_logging_enabled(
    *,
    environment: str,
    configured_value: bool | None,
) -> bool:
    if configured_value is not None:
        return configured_value
    return is_dev_environment(environment)


def build_llm_request_log(
    *,
    endpoint: str,
    headers: Mapping[str, Any],
    payload: Mapping[str, Any],
    include_content: bool,
) -> dict[str, Any]:
    messages = payload.get("messages")
    tools = payload.get("tools")
    base = {
        "endpoint": endpoint,
        "model": payload.get("model"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "tool_choice": payload.get("tool_choice"),
        "response_format": sanitize_for_logging(payload.get("response_format")),
        "headers": sanitize_for_logging(dict(headers)),
    }
    if include_content:
        base["payload"] = sanitize_for_logging(payload)
    else:
        base["payload_summary"] = summarize_llm_payload(payload)
    return base


def build_llm_response_log(
    *,
    body: Mapping[str, Any],
    include_content: bool,
) -> dict[str, Any]:
    if include_content:
        return {"raw_body": sanitize_for_logging(body)}
    return {"raw_body_summary": summarize_llm_response(body)}


def summarize_llm_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": payload.get("model"),
        "temperature": payload.get("temperature"),
        "max_tokens": payload.get("max_tokens"),
        "max_completion_tokens": payload.get("max_completion_tokens"),
        "tool_choice": payload.get("tool_choice"),
        "response_format": sanitize_for_logging(payload.get("response_format")),
        "messages": summarize_llm_messages(payload.get("messages")),
        "tools": summarize_llm_tools(payload.get("tools")),
    }


def summarize_llm_response(body: Mapping[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    choice_items: list[Any] = []
    if isinstance(choices, list):
        for item in choices[:5]:
            if not isinstance(item, Mapping):
                continue
            message = item.get("message")
            choice_items.append({
                "finish_reason": item.get("finish_reason"),
                "index": item.get("index"),
                "message": summarize_llm_message(message),
            })
    return {
        "id": body.get("id"),
        "model": body.get("model"),
        "choices": choice_items,
        "usage": sanitize_for_logging(body.get("usage")),
    }


def summarize_llm_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:50]:
        result.append(summarize_llm_message(item))
    return result


def summarize_llm_message(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"type": type(value).__name__}
    content = value.get("content")
    tool_calls = value.get("tool_calls")
    return {
        "role": value.get("role"),
        "content_chars": _content_length(content),
        "content_sha256": _content_hash(content),
        "tool_calls": summarize_llm_tools(tool_calls),
        "name": value.get("name"),
        "tool_call_id": value.get("tool_call_id"),
    }


def summarize_llm_tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    tools: list[dict[str, Any]] = []
    for item in value[:50]:
        if not isinstance(item, Mapping):
            tools.append({"type": type(item).__name__})
            continue
        function = item.get("function")
        if isinstance(function, Mapping):
            arguments = function.get("arguments")
            tools.append({
                "name": function.get("name"),
                "arguments_chars": _content_length(arguments),
                "arguments_sha256": _content_hash(arguments),
            })
            continue
        tools.append(sanitize_for_logging(item))
    return tools


def _add_static_context(
    _: Any,
    __: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    for key, value in _STATIC_CONTEXT.items():
        event_dict.setdefault(key, value)
    return event_dict


def _add_trace_context(
    _: Any,
    __: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    for key, value in _trace_context_fields().items():
        event_dict.setdefault(key, value)
    return event_dict


def _trace_context_fields() -> dict[str, str]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def _sanitize_event_dict(
    _: Any,
    __: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: _sanitize_value(
            value,
            max_string_length=8000,
            max_collection_items=100,
            max_depth=8,
            depth=0,
            field_name=key,
        )
        for key, value in event_dict.items()
    }


def _sanitize_value(
    value: Any,
    *,
    max_string_length: int,
    max_collection_items: int,
    max_depth: int,
    depth: int,
    field_name: str | None,
) -> Any:
    if field_name and field_name.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    if depth >= max_depth:
        return "[max_depth]"
    if isinstance(value, str):
        return _truncate(value, max_string_length)
    if isinstance(value, bytes):
        return _truncate(value.decode("utf-8", errors="replace"), max_string_length)
    if isinstance(value, BaseModel):
        return _sanitize_value(
            value.model_dump(mode="json"),
            max_string_length=max_string_length,
            max_collection_items=max_collection_items,
            max_depth=max_depth,
            depth=depth + 1,
            field_name=field_name,
        )
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(key): _sanitize_value(
                item_value,
                max_string_length=max_string_length,
                max_collection_items=max_collection_items,
                max_depth=max_depth,
                depth=depth + 1,
                field_name=str(key),
            )
            for key, item_value in items[:max_collection_items]
        }
        if len(items) > max_collection_items:
            result["_truncated_items"] = len(items) - max_collection_items
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = list(value[:max_collection_items])
        result = [
            _sanitize_value(
                item,
                max_string_length=max_string_length,
                max_collection_items=max_collection_items,
                max_depth=max_depth,
                depth=depth + 1,
                field_name=field_name,
            )
            for item in items
        ]
        if len(value) > max_collection_items:  # type: ignore[arg-type]
            result.append(f"[+{len(value) - max_collection_items} more items]")  # type: ignore[arg-type]
        return result
    return value


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max_length]}{_TRUNCATED}[{len(value) - max_length} chars truncated]"


def _content_length(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        try:
            rendered = orjson.dumps(value)
        except TypeError:
            return None
        return len(rendered)
    return len(str(value))


def _content_hash(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        try:
            raw = orjson.dumps(value)
        except TypeError:
            raw = str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _json_serializer(value: Any, **_: Any) -> str:
    return orjson.dumps(
        value,
        default=lambda item: f"<{type(item).__name__}>",
        option=orjson.OPT_NON_STR_KEYS,
    ).decode("utf-8")
