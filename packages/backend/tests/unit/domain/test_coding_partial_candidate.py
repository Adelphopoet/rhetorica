import asyncio
import hashlib
import io
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

from rhetorica_backend.domain.coding.executor import GovernedCodingExecutor


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            bundle.addfile(entry, io.BytesIO(content))
    return output.getvalue()


def test_interrupted_developer_retains_actual_diff_without_qa_or_publication():
    base = archive({"dbt_dg/model.sql": b"old", "dbt_dg/removed.sql": b"remove"})
    partial = archive({"dbt_dg/model.sql": b"new", "dbt_dg/test.sql": b"test"})
    executor = object.__new__(GovernedCodingExecutor)
    executor.runner = Mock()
    executor.runner.collect.return_value = partial
    executor._persist = Mock(return_value="partial-artifact")
    executor._artifact = Mock(return_value=base)
    executor.publisher = Mock()
    executor.service = Mock()
    executor._tasks = Mock(
        return_value=[
            SimpleNamespace(id="root", role="coordinator", checkpoint_ref="base"),
            SimpleNamespace(id="dev", role="developer"),
        ]
    )
    admission = SimpleNamespace(
        run=SimpleNamespace(
            candidate=None,
            id="run",
            workspace_id="ws",
            owner_user_id="owner",
            repository=SimpleNamespace(base_sha="a" * 40),
        )
    )
    assert asyncio.run(executor.cancel(admission=admission)) == ("partial-artifact",)
    candidate = executor.service.runs.record_candidate.call_args.args[0]
    assert candidate.changed_paths == ["dbt_dg/model.sql", "dbt_dg/removed.sql", "dbt_dg/test.sql"]
    assert candidate.digest == hashlib.sha256(partial).hexdigest()
    executor.runner.close.assert_called_once_with("dev")
    executor.publisher.publish.assert_not_called()
