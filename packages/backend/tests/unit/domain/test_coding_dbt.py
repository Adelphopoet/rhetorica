from __future__ import annotations

import json

import pytest
from rhetorica_backend.contracts.coding import DbtCommand, DbtRunRequest, ValidationStatus
from rhetorica_backend.domain.coding.dbt import DbtAdmissionError, DbtCommandPolicy


def test_data_test_argv_forces_data_test_selector_without_shell_flags(tmp_path) -> None:
    request = DbtRunRequest(
        command=DbtCommand.DATA_TEST,
        selectors=["state:modified+"],
        excludes=["tag:slow"],
        variables={"tenant": "analytics"},
    )

    argv = DbtCommandPolicy().argv(
        request,
        project_dir=tmp_path / "project",
        profiles_dir=tmp_path / "profiles",
        profile_name="rhetorica",
    )

    assert argv[:2] == ["dbt", "test"]
    assert "test_type:data,state:modified+" in argv
    assert "tag:slow" in argv
    assert "run" not in argv
    assert "build" not in argv


def test_list_argv_omits_unsupported_threads_option(tmp_path) -> None:
    argv = DbtCommandPolicy().argv(
        DbtRunRequest(command=DbtCommand.LIST),
        project_dir=tmp_path / "project",
        profiles_dir=tmp_path / "profiles",
        profile_name="rhetorica",
    )

    assert argv[:2] == ["dbt", "ls"]
    assert "--threads" not in argv
    assert argv[argv.index("--output") + 1] == "json"
    assert "description" in argv and "columns" in argv and "config" in argv


@pytest.mark.parametrize("command", ["run", "build", "seed", "snapshot", "clone", "run-operation"])
def test_unsafe_dbt_commands_are_not_in_contract(command: str) -> None:
    with pytest.raises(ValueError):
        DbtRunRequest(command=command)


def test_manifest_rejects_store_failures_and_persistent_failure_materialization(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    policy = DbtCommandPolicy()
    path.write_text(
        json.dumps({
            "nodes": {
                "test.project.one": {
                    "resource_type": "test",
                    "config": {"store_failures": True, "store_failures_as": "table"},
                }
            }
        }),
        encoding="utf-8",
    )

    with pytest.raises(DbtAdmissionError, match="dbt_store_failures_forbidden"):
        policy.validate_test_manifest(path)


def test_data_test_receipt_marks_baseline_and_timeout_not_passed() -> None:
    request = DbtRunRequest(command=DbtCommand.DATA_TEST)

    receipt = DbtCommandPolicy().receipt(
        request=request,
        exit_code=124,
        duration_ms=1000,
        stdout="",
        incomplete=True,
        reason="dbt_command_timeout",
    )

    assert receipt.status == ValidationStatus.BLOCKED
    assert receipt.evidence_scope == "baseline"
    assert receipt.materialization_status == "not_run"


@pytest.mark.parametrize("command", ["parse", "list", "show", "data_test"])
def test_no_introspect_is_only_allowed_for_compile(command):
    options = {"selectors": ["model"], "show_limit": 5} if command == "show" else {}
    with pytest.raises(ValueError, match="dbt_no_introspect_requires_compile"):
        DbtRunRequest(command=command, no_introspect=True, **options)


@pytest.mark.parametrize("command", ["parse", "list"])
def test_discovery_commands_reject_cache_flags_before_starting_dbt(command):
    with pytest.raises(ValueError, match="dbt_no_populate_cache_not_supported"):
        DbtRunRequest(command=command, no_populate_cache=True)


def test_empty_selection_cannot_be_reported_as_a_successful_dbt_check():
    import json
    from pathlib import Path

    policy = DbtCommandPolicy()
    for command in ("list", "compile", "show", "data_test"):
        argv = policy.argv(
            DbtRunRequest(
                command=command,
                selectors=["missing_model"],
                show_limit=1 if command == "show" else None,
            ),
            project_dir=Path("project"),
            profiles_dir=Path("profile"),
            profile_name="rhetorica",
        )
        assert json.loads(argv[argv.index("--warn-error-options") + 1]) == {
            "error": ["NoNodesForSelectionCriteria", "NothingToDo"]
        }


def test_show_previews_only_the_selected_model_without_attached_tests():
    from pathlib import Path

    argv = DbtCommandPolicy().argv(
        DbtRunRequest(command="show", selectors=["new_model"], show_limit=5),
        project_dir=Path("project"),
        profiles_dir=Path("profile"),
        profile_name="rhetorica",
    )

    assert argv[argv.index("--indirect-selection") + 1] == "empty"
    assert argv[argv.index("--select") + 1] == "new_model"
