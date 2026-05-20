#!/usr/bin/env python3
"""Run live QA scenarios against the streaming chat API."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live manual QA scenarios against /v1/chat/ask/stream.",
    )
    parser.add_argument("questions", type=Path, help="Path to scenarios JSON file.")
    parser.add_argument("results", type=Path, help="Path to output results JSON file.")
    parser.add_argument(
        "--report-md",
        type=Path,
        default=None,
        help="Optional markdown report output path.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API base URL.",
    )
    parser.add_argument(
        "--workspace-id",
        default="default",
        help="Default workspace id for scenarios without explicit workspace_id.",
    )
    parser.add_argument(
        "--login",
        default="admin",
        help="Login for auth bootstrap. Pass empty string to skip auth login.",
    )
    parser.add_argument(
        "--password",
        default="admin",
        help="Password for auth bootstrap. Pass empty string to skip auth login.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Max concurrent scenarios. Use 1 by default for stable debugging.",
    )
    parser.add_argument(
        "--include-chunks",
        action="store_true",
        help="Persist chunk SSE events too.",
    )
    return parser.parse_args()

def load_scenarios(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def login_for_token(
    *,
    base_url: str,
    login: str,
    password: str,
    timeout_seconds: int,
) -> str | None:
    if not login or not password:
        return None
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/auth/login",
        json={"login": login, "password": password},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("access_token")


def build_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def normalize_scenario(
    scenario: dict[str, Any],
    *,
    workspace_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = dict(scenario)
    payload = {
        "question": normalized["question"],
        "workspace_id": normalized.get("workspace_id", workspace_id),
        "run_sql": normalized.get("run_sql", False),
    }
    return normalized, payload


def parse_sse_events(
    response: requests.Response,
    *,
    include_chunks: bool,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    terminal_event_type: str | None = None
    terminal_payload: dict[str, Any] | None = None
    event_type: str | None = None
    event_data: list[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line:
            if event_type is not None:
                payload: dict[str, Any]
                raw_payload = "\n".join(event_data)
                if raw_payload:
                    try:
                        parsed = json.loads(raw_payload)
                    except json.JSONDecodeError:
                        parsed = {"raw": raw_payload}
                else:
                    parsed = {}
                payload = dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
                payload["_event_type"] = event_type
                if include_chunks or event_type != "chunk":
                    events.append(payload)
                if event_type in {"done", "error"}:
                    terminal_event_type = event_type
                    terminal_payload = payload
                    break
            event_type = None
            event_data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
            continue
        if line.startswith("data:"):
            event_data.append(line[5:].strip())

    return events, terminal_event_type, terminal_payload


def build_tool_attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []

    for event in events:
        event_type = event.get("_event_type")
        tool_call_id = event.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            continue
        if event_type == "tool_call_start":
            starts[tool_call_id] = event
            continue
        if event_type != "tool_call_end":
            continue
        start = starts.get(tool_call_id, {})
        attempts.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": event.get("tool_name") or start.get("tool_name"),
                "success": event.get("success"),
                "result_summary": event.get("result_summary"),
                "arguments": start.get("arguments"),
            }
        )
    return attempts


def classify_result(item: dict[str, Any]) -> str:
    if item.get("status_code") != 200:
        return "FAIL"
    body = item.get("response") or {}
    execution = body.get("execution") or {}
    warnings = body.get("warnings") or []
    if item.get("terminal_event_type") == "error":
        return "FAIL"
    if execution.get("status") in {"failed", "blocked", "cancelled"}:
        return "FAIL"
    if any(
        warning in warnings
        for warning in (
            "database_connector_not_selected",
            "sql_preflight_failed",
            "sql_execution_failed",
            "llm_tool_loop_fatal_stop",
            "llm_tool_loop_limit_reached",
        )
    ):
        return "FAIL"
    if body.get("generated_sql") and execution.get("status") != "succeeded":
        return "PARTIAL"
    if body.get("metadata_sources") and not body.get("claims") and not body.get("evidence"):
        return "PARTIAL"
    return "OK"


def short_conclusion(item: dict[str, Any]) -> str:
    body = item.get("response") or {}
    execution = body.get("execution") or {}
    result = classify_result(item)
    if result == "OK":
        return "Сценарий выглядит рабочим."
    if result == "PARTIAL":
        return "Ответ частично полезен, но контракт или grounding просели."
    if execution.get("status") == "blocked":
        return "SQL execution заблокирован."
    if "database_connector_not_selected" in (body.get("warnings") or []):
        return "run_sql не смог выбрать коннектор."
    if "sql_preflight_failed" in (body.get("warnings") or []):
        return "SQL preflight не прошел."
    return "Сценарий сломан или ответ недостоверен."


def write_report(results: list[dict[str, Any]], report_path: Path) -> None:
    latencies = [
        item["total_latency_s"]
        for item in results
        if isinstance(item.get("total_latency_s"), int | float)
    ]
    lines: list[str] = []
    lines.append("# Live API Manual QA Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Scenarios: {len(results)}")
    lines.append(f"- OK: {sum(1 for item in results if classify_result(item) == 'OK')}")
    lines.append(
        f"- PARTIAL: {sum(1 for item in results if classify_result(item) == 'PARTIAL')}"
    )
    lines.append(f"- FAIL: {sum(1 for item in results if classify_result(item) == 'FAIL')}")
    if latencies:
        lines.append(f"- Total latency min: {min(latencies)}s")
        lines.append(f"- Total latency max: {max(latencies)}s")
        lines.append(f"- Total latency avg: {round(sum(latencies) / len(latencies), 2)}s")
        lines.append(f"- Total latency median: {round(statistics.median(latencies), 2)}s")
    lines.append("")
    lines.append("## Detailed Results")
    lines.append("")

    for item in results:
        body = item.get("response") or {}
        execution = body.get("execution") or {}
        attempts = item.get("tool_attempts") or []
        lines.append(f"### {item.get('id')} {item.get('name')}")
        lines.append("")
        lines.append(f"- Result: `{classify_result(item)}`")
        lines.append(f"- Question: `{item.get('question')}`")
        lines.append(f"- Status code: `{item.get('status_code')}`")
        lines.append(f"- Total latency: `{item.get('total_latency_s')}s`")
        lines.append(f"- Terminal event: `{item.get('terminal_event_type')}`")
        lines.append(f"- Conclusion: {short_conclusion(item)}")
        lines.append(f"- Tool attempts: `{len(attempts)}`")
        lines.append(f"- Execution status: `{execution.get('status')}`")
        lines.append(f"- Warnings: `{body.get('warnings')}`")
        lines.append("")
        lines.append("Request payload:")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(item.get("request_payload", {}), ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append("Response details:")
        lines.append("")
        lines.append(f"- Answer: {body.get('answer') or ''}")
        lines.append(f"- Generated SQL: `{body.get('generated_sql')}`")
        lines.append(f"- Selected connector: `{body.get('selected_database_connector')}`")
        lines.append(f"- Metadata sources: `{body.get('metadata_sources')}`")
        lines.append(f"- Context tables: `{body.get('context_tables')}`")
        lines.append(f"- Claims count: `{len(body.get('claims') or [])}`")
        lines.append(f"- Evidence count: `{len(body.get('evidence') or [])}`")
        lines.append("")
        if attempts:
            lines.append("Tool attempts:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(attempts, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

    report_path.write_text("\n".join(lines))


def run_scenario(
    *,
    scenario: dict[str, Any],
    payload: dict[str, Any],
    normalized_scenario: dict[str, Any],
    headers: dict[str, str],
    base_url: str,
    timeout_seconds: int,
    include_chunks: bool,
) -> dict[str, Any]:
    started_at = time.time()
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/ask/stream",
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
            stream=True,
        )
        events, terminal_event_type, terminal_payload = parse_sse_events(
            response,
            include_chunks=include_chunks,
        )
        total_latency_s = round(time.time() - started_at, 2)
        body = terminal_payload or {"stream_error": "missing_terminal_event"}
        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "question": scenario["question"],
            "original_scenario": scenario,
            "normalized_scenario": normalized_scenario,
            "request_payload": payload,
            "status_code": response.status_code,
            "total_latency_s": total_latency_s,
            "terminal_event_type": terminal_event_type,
            "response": body,
            "tool_attempts": build_tool_attempts(events),
            "events": events,
        }
    except Exception as exc:
        total_latency_s = round(time.time() - started_at, 2)
        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "question": scenario["question"],
            "original_scenario": scenario,
            "normalized_scenario": normalized_scenario,
            "request_payload": payload,
            "status_code": None,
            "total_latency_s": total_latency_s,
            "terminal_event_type": None,
            "error": repr(exc),
            "traceback": traceback.format_exc(),
            "tool_attempts": [],
            "events": [],
        }


def persist_results(
    *,
    results_path: Path,
    scenarios: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    report_path: Path | None,
) -> None:
    ordered = [results[item["id"]] for item in scenarios if item["id"] in results]
    results_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2))
    if report_path is not None:
        write_report(ordered, report_path)


def main() -> int:
    args = parse_args()
    scenarios = load_scenarios(args.questions)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    if args.report_md is not None:
        args.report_md.parent.mkdir(parents=True, exist_ok=True)

    token = login_for_token(
        base_url=args.base_url,
        login=args.login,
        password=args.password,
        timeout_seconds=min(args.timeout_seconds, 30),
    )
    headers = build_headers(token)

    conversation_ids: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    submitted: set[str] = set()
    future_to_id: dict[Any, str] = {}

    def submit(
        executor: ThreadPoolExecutor,
        scenario: dict[str, Any],
        conversation_id: str | None = None,
    ) -> None:
        normalized_scenario, payload = normalize_scenario(
            scenario,
            workspace_id=args.workspace_id,
        )
        if conversation_id:
            payload["conversation_id"] = conversation_id
        future = executor.submit(
            run_scenario,
            scenario=scenario,
            payload=payload,
            normalized_scenario=normalized_scenario,
            headers=headers,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            include_chunks=args.include_chunks,
        )
        future_to_id[future] = scenario["id"]
        submitted.add(scenario["id"])
        if conversation_id:
            print(f"[submit] {scenario['id']} via {scenario['conversation_from']}", flush=True)
        else:
            print(f"[submit] {scenario['id']}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        for scenario in scenarios:
            if scenario.get("conversation_from"):
                continue
            submit(executor, scenario)

        while future_to_id:
            done, _ = wait(set(future_to_id.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                scenario_id = future_to_id.pop(future)
                result = future.result()
                results[scenario_id] = result

                body = result.get("response") or {}
                conversation_id = body.get("conversation_id")
                if isinstance(conversation_id, str) and conversation_id.strip():
                    conversation_ids[scenario_id] = conversation_id

                execution = body.get("execution") or {}
                warnings = body.get("warnings") or []
                print(
                    (
                        f"[done] {scenario_id} status={result.get('status_code')} "
                        f"total_latency={result.get('total_latency_s')}s "
                        f"terminal={result.get('terminal_event_type')} "
                        f"exec={execution.get('status')} "
                        f"warnings={','.join(warnings[:3])}"
                    ),
                    flush=True,
                )
                persist_results(
                    results_path=args.results,
                    scenarios=scenarios,
                    results=results,
                    report_path=args.report_md,
                )

                for scenario in scenarios:
                    parent_id = scenario.get("conversation_from")
                    child_id = scenario["id"]
                    if not parent_id or child_id in submitted:
                        continue
                    if parent_id in conversation_ids:
                        submit(
                            executor,
                            scenario,
                            conversation_id=conversation_ids[parent_id],
                        )

    ordered = [results[item["id"]] for item in scenarios if item["id"] in results]
    print(f"Saved {len(ordered)} results to {args.results}", flush=True)
    if args.report_md is not None:
        print(f"Saved report to {args.report_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
