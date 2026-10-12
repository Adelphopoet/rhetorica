"""Bounded public tool metadata; never publish file bodies, SQL rows or stdout."""

import json
import re

from rhetorica_backend.domain.coding.publication_content_policy import PublicationSecretScanner

_SCANNER = PublicationSecretScanner()
_SENSITIVE = re.compile(r"password|secret|token|api[_-]?key|authorization|credential", re.I)
_FIELDS = frozenset({
    "path", "path_prefix", "asset_path", "offset", "limit", "max_chars", "field",
    "call_id", "skill_id", "command", "project_subdir", "selectors", "excludes",
    "show_limit", "threads", "no_introspect", "no_populate_cache",
    "sql", "query", "row_limit", "timeout_ms",
})


def bounded_text(value: str, limit: int) -> str:
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(value[:middle]).encode()) <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _safe_text(value: str) -> str:
    if _SCANNER.reason_for(path="arguments.json", content=value.encode()):
        return "[скрыто]"
    if re.search(r"://[^\s/]+@", value) or _SENSITIVE.search(value):
        return "[скрыто]"
    visible = bounded_text(value, 512)
    return visible if visible == value else visible + "… (сокращено)"


def visible_arguments(arguments: dict) -> dict:
    source = arguments.get("request", arguments)
    if not isinstance(source, dict):
        return {}
    visible = {}
    for key, value in source.items():
        if key == "argv" and isinstance(value, list):
            argv, hide_next = [], False
            for part in value[:32]:
                if not isinstance(part, str):
                    continue
                argv.append("[скрыто]" if hide_next else _safe_text(part))
                hide_next = bool(_SENSITIVE.search(part)) and "=" not in part
            visible[key] = argv
            if len(value) > 32:
                visible["truncated"] = True
        elif key in _FIELDS:
            if isinstance(value, str):
                visible[key] = _safe_text(value)
            elif isinstance(value, int | float | bool):
                visible[key] = value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                visible[key] = [_safe_text(item) for item in value[:20]]
                if len(value) > 20:
                    visible["truncated"] = True
    # Leave room for the event envelope and escaped Unicode in the 4 KiB journal limit.
    if len(json.dumps(visible).encode()) > 2400:
        return {"preview": bounded_text(json.dumps(visible, ensure_ascii=False), 2100), "truncated": True}
    return visible


def visible_result(value: dict) -> tuple[bool, str]:
    status = value.get("status")
    success = status not in {"denied", "failed", "blocked", "invalid_arguments", "error", "cancelled"}
    if "exit_code" in value:
        success = success and value["exit_code"] == 0
    parts = ["Выполнено" if success else "Ошибка или отказ"]
    if "exit_code" in value:
        parts.append(f"exit code: {value['exit_code']}")
    if isinstance(value.get("duration_ms"), int | float):
        parts.append(f"{value['duration_ms']} ms")
    if isinstance(value.get("reason_code"), str):
        parts.append(_safe_text(value["reason_code"]))
    if value.get("truncated") or value.get("prompt_truncated"):
        parts.append("результат сокращён")
    checks = value.get("checks")
    if isinstance(checks, list):
        for check in checks[:8]:
            receipt = check.get("dbt_receipt", {}) if isinstance(check, dict) else {}
            if isinstance(receipt, dict):
                command = receipt.get("command")
                if command in {"parse", "list", "compile", "show", "data_test"}:
                    parts.append(f"{command}: {receipt.get('status', 'unknown')}")
    return success, bounded_text(" · ".join(parts), 1800)
