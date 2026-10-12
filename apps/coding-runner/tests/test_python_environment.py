import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from rhetorica_backend.contracts.coding import PreparePythonEnvironmentRequest
from rhetorica_coding_runner.python_environment import PythonEnvironmentPreparer


def request(**overrides):
    return PreparePythonEnvironmentRequest.model_validate({
        "profile_id": "env",
        "profile_revision": 1,
        "pip_dependencies": ["six==1.17.0"],
        "dependency_sources": ["https://pypi.org/simple"],
        **overrides,
    })


@pytest.mark.parametrize(
    "requirement",
    [
        "six",
        "six>=1",
        "six==1.*",
        "--index-url=evil",
        "six @ https://evil/six.whl",
        "six==1; python_version<'3'",
        "../six==1",
        "six==1\nother==2",
    ],
)
def test_rejects_unpinned_packages_and_pip_options(requirement):
    with pytest.raises(ValidationError):
        PreparePythonEnvironmentRequest(
            profile_id="env", profile_revision=1, pip_dependencies=[requirement]
        )


@pytest.mark.parametrize(
    "source",
    [
        "http://pypi.org/simple",
        "https://user:secret@pypi.org/simple",
        "https://pypi.org/simple?token=secret",
        "https://",
    ],
)
def test_rejects_non_https_and_credential_indexes(source):
    with pytest.raises(ValidationError):
        PreparePythonEnvironmentRequest(
            profile_id="env", profile_revision=1, dependency_sources=[source]
        )


def preparer(tmp_path, *, failure=False):
    def run(operation, argv, *args, **kwargs):
        if "venv" in argv:
            root = tmp_path / "python-env"
            (root / "bin").mkdir(parents=True)
            (
                root
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            ).mkdir(parents=True)
        return {
            "exit_code": 1 if failure and "install" in argv else 0,
            "stderr": "missing",
            "stdout": "six==1.17.0\n",
        }

    runner = Mock(side_effect=run)
    return PythonEnvironmentPreparer(tmp_path, runner), runner


def test_preparation_is_idempotent_bound_to_recipe_and_outside_repository(tmp_path):
    env, run = preparer(tmp_path)
    first = env.prepare(request())
    assert first["resolved_dependencies"] == ["six==1.17.0"]
    before = run.call_count
    assert env.prepare(request()) == first
    assert run.call_count == before
    assert env.environment()["VIRTUAL_ENV"] == str(tmp_path / "python-env")
    install = next(call.args[1] for call in run.call_args_list if "install" in call.args[1])
    assert "--only-binary=:all:" in install and "--isolated" in install
    with pytest.raises(ValueError, match="recipe_conflict"):
        env.prepare(request(profile_revision=2))


def test_failed_install_never_publishes_prepared_environment(tmp_path):
    env, _ = preparer(tmp_path, failure=True)
    with pytest.raises(ValueError, match="coding_dependency_install_failed"):
        env.prepare(request())
    assert env.environment() == {}
    assert not env.root.exists()
