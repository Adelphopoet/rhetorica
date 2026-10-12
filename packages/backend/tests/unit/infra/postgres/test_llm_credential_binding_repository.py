from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from rhetorica_backend.infra.postgres.llm_credential_binding_repository import (
    LlmCredentialBindingRepository,
    LlmCredentialsUnavailableError,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime


def _repository(tmp_path: Path) -> LlmCredentialBindingRepository:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path / 'llm_binding.db'}")
    runtime.create_schema()
    return LlmCredentialBindingRepository(runtime)


def test_binding_is_created_reused_and_invalidated_by_version(tmp_path: Path, monkeypatch) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_credential_binding_repository.secrets.randbelow",
        lambda count: count - 1,
    )

    first = repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=2,
    )
    reused = repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=2,
    )
    refreshed = repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=2,
        credential_count=1,
    )

    assert first == 1
    assert reused == 1
    assert refreshed == 0


def test_rebind_is_conditional_on_current_binding(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=2,
    )

    assert repository.rebind(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        expected_index=1 - first,
        credential_set_version=1,
        new_index=1,
    ) is False
    assert repository.rebind(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        expected_index=first,
        credential_set_version=1,
        new_index=0,
    ) is True


def test_empty_credentials_do_not_create_binding(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(LlmCredentialsUnavailableError):
        repository.resolve(
            conversation_id="conversation-1",
            llm_connector_id="connector-1",
            credential_set_version=1,
            credential_count=0,
        )

    assert repository._get(repository._db.session(), "conversation-1", "connector-1") is None


def test_binding_recovers_from_out_of_range_index_after_key_removal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_credential_binding_repository.secrets.randbelow",
        lambda count: count - 1,
    )
    assert repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=3,
    ) == 2

    # A supported removal or reorder increments the connector-set version.  The
    # old index must not escape into the new, one-key snapshot.
    assert repository.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=2,
        credential_count=1,
    ) == 0

    with repository._db.session() as session:
        binding = repository._get(session, "conversation-1", "connector-1")
        assert binding is not None
        assert binding.credential_index == 0
        assert binding.credential_set_version == 2


def test_binding_is_reused_after_repository_restart(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    first_runtime = DatabaseRuntime(database_url)
    first_runtime.create_schema()
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_credential_binding_repository.secrets.randbelow",
        lambda count: 1,
    )
    first = LlmCredentialBindingRepository(first_runtime)
    assert first.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=2,
    ) == 1

    restarted = LlmCredentialBindingRepository(DatabaseRuntime(database_url))
    assert restarted.resolve(
        conversation_id="conversation-1",
        llm_connector_id="connector-1",
        credential_set_version=1,
        credential_count=2,
    ) == 1


def test_concurrent_first_binding_uses_one_persisted_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        "rhetorica_backend.infra.postgres.llm_credential_binding_repository.secrets.randbelow",
        lambda _count: 1,
    )
    barrier = Barrier(2)

    def resolve() -> int:
        barrier.wait(timeout=2)
        return repository.resolve(
            conversation_id="conversation-1",
            llm_connector_id="connector-1",
            credential_set_version=1,
            credential_count=2,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _unused: resolve(), range(2)))

    assert results == [1, 1]
    with repository._db.session() as session:
        binding = repository._get(session, "conversation-1", "connector-1")
        assert binding is not None
        assert binding.credential_index == 1
