"""Opt-in live acceptance for the coding Trino gateway.

Run inside the pinned coding-runner image after issuing a short-lived profile.
The probes target a deliberately nonexistent catalog, so they remain non-mutating
even if a regression lets a statement reach Trino.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from rhetorica_backend.contracts.coding import DbtRunRequest, DbtRuntimeProfile
from rhetorica_coding_runner.contracts import ExecuteRequest
from rhetorica_coding_runner.runtime import ExecutionRuntime
from rhetorica_coding_runner.settings import CodingRunnerSettings


def _profile() -> tuple[DbtRuntimeProfile, str]:
    payload = json.loads(Path(os.environ["CODING_LIVE_DBT_PROFILE_JSON"]).read_text())
    profile = DbtRuntimeProfile.model_validate(payload["runtime_profile"])
    document = json.loads(profile.profiles_yml)
    output = document[profile.profile_name]["outputs"]["gateway"]
    return profile, output["http_headers"]["X-Rhetorica-Query-Capability"]


def _query(gateway_url: str, capability: str, sql: str) -> list[Any]:
    headers = {"X-Rhetorica-Query-Capability": capability}
    rows: list[Any] = []
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{gateway_url}/v1/statement", headers=headers, content=sql)
        response.raise_for_status()
        page = response.json()
        rows.extend(page.get("data") or [])
        while page.get("nextUri"):
            response = client.get(page["nextUri"], headers=headers)
            response.raise_for_status()
            page = response.json()
            rows.extend(page.get("data") or [])
    return rows


def _blocked(
    gateway_url: str,
    capability: str,
    sql: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> None:
    headers = {"X-Rhetorica-Query-Capability": capability, **(extra_headers or {})}
    response = httpx.post(
        f"{gateway_url}/v1/statement", headers=headers, content=sql, timeout=30
    )
    if response.status_code != 422:
        raise AssertionError((sql, response.status_code, response.text[:500]))


def _run_dbt(
    runtime: ExecutionRuntime,
    profile: DbtRuntimeProfile,
    request: DbtRunRequest,
    *,
    expected_status: str,
) -> None:
    result = runtime.execute(
        ExecuteRequest(
            operation="run_dbt",
            timeout_seconds=120,
            dbt_request=request,
            dbt_runtime_profile=profile,
        )
    )
    receipt = result["dbt_receipt"]
    if receipt["status"] != expected_status:
        raise AssertionError((request.command, receipt, result["stdout"][-2_000:]))


def main() -> None:
    profile, capability = _profile()
    gateway_url = os.environ.get("CODING_LIVE_GATEWAY_URL", "http://127.0.0.1:8092")
    project = Path(os.environ["CODING_LIVE_DBT_PROJECT_DIR"])
    scratch = Path(os.environ.get("CODING_LIVE_DBT_SCRATCH_DIR", "/scratch/live-dbt/runner"))
    runtime = ExecutionRuntime(
        CodingRunnerSettings(
            task_token="live-acceptance",
            workspace_root=project,
            scratch_root=scratch,
        )
    )

    before = _query(gateway_url, capability, "show tables from iceberg_dg.iceberg_dg")
    for spec in (
        {"command": "parse"},
        {"command": "list"},
        {"command": "compile"},
        {"command": "show", "selectors": ["safe_model"], "show_limit": 5},
        {"command": "data_test", "selectors": ["no_rows"]},
    ):
        _run_dbt(runtime, profile, DbtRunRequest.model_validate(spec), expected_status="passed")

    for sql in (
        "create table rhetorica_forbidden_catalog.schema.probe (id bigint)",
        "insert into rhetorica_forbidden_catalog.schema.probe values (1)",
        "call system.rhetorica_forbidden_probe()",
        "explain analyze select 1",
        "select system.rhetorica_unknown_function(1)",
        "select 1; select 2",
    ):
        _blocked(gateway_url, capability, sql)
    _blocked(
        gateway_url,
        capability,
        "select 1",
        extra_headers={"X-Trino-Prepared-Statement": "probe=select+1"},
    )

    _run_dbt(
        runtime,
        profile,
        DbtRunRequest(
            command="compile",
            selectors=["malicious_compile"],
            variables={"enable_malicious_compile": True},
        ),
        expected_status="failed",
    )
    _run_dbt(
        runtime,
        profile,
        DbtRunRequest(
            command="data_test",
            selectors=["malicious_custom"],
            variables={"enable_malicious_test": True},
        ),
        expected_status="failed",
    )
    _run_dbt(
        runtime,
        profile,
        DbtRunRequest(
            command="data_test",
            selectors=["no_rows"],
            variables={"enable_malicious_hook": True},
        ),
        expected_status="failed",
    )
    after = _query(gateway_url, capability, "show tables from iceberg_dg.iceberg_dg")
    if before != after:
        raise AssertionError("trino_metadata_changed_during_read_only_acceptance")
    print(json.dumps({"dbt_commands": 5, "blocked_probes": 10, "metadata_unchanged": True}))


if __name__ == "__main__":
    main()
