"""Typed dbt command admission and evidence classification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from rhetorica_backend.contracts.coding import (
    DbtCommand,
    DbtCommandReceipt,
    DbtEvidenceScope,
    DbtRunRequest,
    ValidationStatus,
)


class DbtAdmissionError(ValueError):
    pass


class DbtCommandPolicy:
    """Build argv from structured input; arbitrary dbt flags never cross this boundary."""

    def argv(
        self,
        request: DbtRunRequest,
        *,
        project_dir: Path,
        profiles_dir: Path,
        profile_name: str,
    ) -> list[str]:
        command = {
            DbtCommand.PARSE: "parse",
            DbtCommand.LIST: "ls",
            DbtCommand.COMPILE: "compile",
            DbtCommand.SHOW: "show",
            DbtCommand.DATA_TEST: "test",
        }[DbtCommand(request.command)]
        argv = [
            "dbt",
            command,
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(profiles_dir),
            "--profile",
            profile_name,
            "--no-use-colors",
            "--warn-error-options",
            json.dumps({"error": ["NoNodesForSelectionCriteria", "NothingToDo"]}),
        ]
        if request.command == DbtCommand.LIST:
            argv.extend([
                "--quiet",
                "--output",
                "json",
                "--output-keys",
                "unique_id",
                "name",
                "original_file_path",
                "description",
                "columns",
                "config",
            ])
        else:
            argv.extend(["--threads", str(request.threads)])
        if request.variables:
            argv.extend([
                "--vars",
                json.dumps(request.variables, sort_keys=True, separators=(",", ":")),
            ])
        if request.no_introspect:
            argv.append("--no-introspect")
        if request.no_populate_cache:
            argv.append("--no-populate-cache")

        selectors = list(request.selectors)
        if request.command == DbtCommand.DATA_TEST:
            selectors = (
                [f"test_type:data,{selector}" for selector in selectors]
                if selectors
                else ["test_type:data"]
            )
        if selectors:
            argv.extend(["--select", *selectors])
        if request.excludes:
            argv.extend(["--exclude", *request.excludes])
        if request.command == DbtCommand.SHOW:
            # Preview only the selected model: attached tests would query a relation
            # that read-only validation never materializes.
            argv.extend(["--indirect-selection", "empty"])
            argv.extend(["--limit", str(request.show_limit)])
        return argv

    def validate_test_manifest(self, manifest_path: Path) -> None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DbtAdmissionError("dbt_manifest_unavailable") from exc
        nodes = manifest.get("nodes")
        if not isinstance(nodes, dict):
            raise DbtAdmissionError("dbt_manifest_invalid")
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("resource_type") != "test":
                continue
            config = node.get("config") or {}
            if not isinstance(config, dict):
                raise DbtAdmissionError("dbt_test_config_invalid")
            if config.get("store_failures") is True:
                raise DbtAdmissionError("dbt_store_failures_forbidden")
            store_as = config.get("store_failures_as")
            if store_as not in {None, "", "ephemeral"}:
                raise DbtAdmissionError("dbt_failure_materialization_forbidden")

    @staticmethod
    def evidence_scope(command: DbtCommand) -> DbtEvidenceScope:
        if command == DbtCommand.DATA_TEST:
            return DbtEvidenceScope.BASELINE
        return DbtEvidenceScope.CANDIDATE

    def receipt(
        self,
        *,
        request: DbtRunRequest,
        exit_code: int | None,
        duration_ms: int,
        stdout: str,
        incomplete: bool = False,
        reason: str | None = None,
    ) -> DbtCommandReceipt:
        status = ValidationStatus.PASSED
        if incomplete:
            status = ValidationStatus.BLOCKED
        elif exit_code not in {0, None}:
            status = ValidationStatus.FAILED
        return DbtCommandReceipt(
            id=str(uuid4()),
            command=request.command,
            status=status,
            evidence_scope=self.evidence_scope(DbtCommand(request.command)),
            exit_code=exit_code,
            duration_ms=duration_ms,
            incomplete=incomplete,
            reason=reason,
            stdout_digest=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        )
