"""Trusted, bounded pip preparation outside the candidate repository."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import sysconfig
import threading
import time
from pathlib import Path

from rhetorica_backend.contracts.coding import PreparePythonEnvironmentRequest


class PythonEnvironmentPreparer:
    def __init__(self, scratch: Path, run):
        self.root = scratch / "python-env"
        self.receipt_path = scratch / "python-environment.json"
        self._run = run
        self._lock = threading.Lock()

    def prepare(self, request: PreparePythonEnvironmentRequest) -> dict:
        recipe = request.model_dump(exclude={"timeout_seconds"})
        digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        with self._lock:
            if self.receipt_path.exists():
                receipt = json.loads(self.receipt_path.read_text())
                if receipt["recipe_digest"] != digest:
                    raise ValueError("coding_environment_recipe_conflict")
                return receipt
            if request.pip_dependencies and not request.dependency_sources:
                raise ValueError("coding_dependency_sources_not_configured")
            started = time.monotonic()

            def run(argv):
                remaining = request.timeout_seconds - int(time.monotonic() - started)
                if remaining <= 0:
                    raise ValueError("coding_dependency_install_timeout")
                result = self._run(
                    "prepare_python_environment",
                    argv,
                    remaining,
                    started=started,
                    env_overrides={
                        "PIP_CONFIG_FILE": os.devnull,
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                        "HOME": str(self.root.parent),
                    },
                    fail_on_truncation=True,
                )
                if result["exit_code"] != 0:
                    raise ValueError(
                        "coding_dependency_install_failed: " + str(result["stderr"])[-2048:]
                    )
                return result["stdout"]

            try:
                shutil.rmtree(self.root, ignore_errors=True)
                run([sys.executable, "-m", "venv", str(self.root)])
                python = str(self.root / "bin/python")
                # The pinned image supplies the base tools; the overlay is per task.
                purelib = (
                    self.root
                    / "lib"
                    / f"python{sys.version_info.major}.{sys.version_info.minor}"
                    / "site-packages"
                )
                (purelib / "rhetorica-base.pth").write_text(sysconfig.get_path("purelib") + "\n")
                if request.pip_dependencies:
                    run([
                        python,
                        "-m",
                        "pip",
                        "--isolated",
                        "--disable-pip-version-check",
                        "install",
                        "--no-input",
                        "--no-cache-dir",
                        "--only-binary=:all:",
                        "--index-url",
                        request.dependency_sources[0],
                        *request.pip_dependencies,
                    ])
                run([python, "-m", "pip", "--isolated", "check"])
                resolved = run([python, "-m", "pip", "--isolated", "freeze", "--all"])
                # Console scripts from the base image must use the overlay interpreter.
                for name, code in {
                    "dbt": "from dbt.cli.main import cli; cli()",
                    "pytest": "import pytest; raise SystemExit(pytest.console_main())",
                }.items():
                    script = self.root / "bin" / name
                    script.write_text(f"#!{python}\n{code}\n")
                    script.chmod(0o700)
                receipt = {
                    **recipe,
                    "recipe_digest": digest,
                    "resolved_dependencies": resolved.splitlines(),
                    "status": "prepared",
                }
                temporary = self.receipt_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(receipt, sort_keys=True))
                temporary.replace(self.receipt_path)
            except BaseException:
                shutil.rmtree(self.root, ignore_errors=True)
                self.receipt_path.unlink(missing_ok=True)
                raise
            else:
                return receipt

    def environment(self) -> dict[str, str]:
        if not self.receipt_path.exists():
            return {}
        return {
            "VIRTUAL_ENV": str(self.root),
            "PATH": str(self.root / "bin") + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
        }
