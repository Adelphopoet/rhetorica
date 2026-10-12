import json
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from rhetorica_backend.contracts.connectors import UpsertConnectorRequest
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.infra.postgres import chat_models as _chat_models  # noqa: F401
from rhetorica_backend.infra.postgres import models as _models  # noqa: F401
from rhetorica_backend.infra.postgres import scenario_models as _scenario_models  # noqa: F401
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.migrations import _has_managed_schema, get_alembic_config
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.secrets import InMemorySecretStore
from sqlalchemy import create_engine, inspect, text


def _sqlite_config(tmp_path: Path) -> Config:
    config = Config()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'test.db'}")
    return config


def test_alembic_revision_graph_has_single_head() -> None:
    heads = ScriptDirectory.from_config(get_alembic_config()).get_heads()

    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"


def test_coding_lease_version_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'coding_lease_version.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    engine = runtime.engine
    config = get_alembic_config()
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE coding_sandbox_lease DROP COLUMN version"))
    command.stamp(config, "b7c8d9e0f1a2")

    command.upgrade(config, "c8d9e0f1a2b3")

    assert "version" in {
        column["name"] for column in inspect(engine).get_columns("coding_sandbox_lease")
    }

    command.downgrade(config, "b7c8d9e0f1a2")

    assert "version" not in {
        column["name"] for column in inspect(engine).get_columns("coding_sandbox_lease")
    }


def test_coding_publication_ledger_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'coding_publication.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    engine = runtime.engine
    config = get_alembic_config()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE coding_publication_operation"))
    command.stamp(config, "d9e0f1a2b3c4")

    command.upgrade(config, "d8e9f0a1b2c3")

    assert "coding_publication_operation" in inspect(engine).get_table_names()
    columns = {
        column["name"] for column in inspect(engine).get_columns("coding_publication_operation")
    }
    assert {
        "id",
        "workspace_id",
        "run_id",
        "state",
        "branch_name",
        "attempt",
        "reason_code",
        "detail",
    } <= columns

    command.downgrade(config, "d9e0f1a2b3c4")

    assert "coding_publication_operation" not in inspect(engine).get_table_names()


def test_durable_coding_run_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'durable_coding_run.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    engine = runtime.engine
    config = get_alembic_config()
    with engine.begin() as connection:
        for table_name in (
            "coding_run_outbox",
            "coding_validation_report",
            "coding_validation_evidence",
            "coding_run_event",
            "coding_iteration_reservation",
            "coding_budget_ledger",
            "coding_agent_task",
            "coding_candidate",
            "coding_run",
        ):
            connection.execute(text(f"DROP TABLE {table_name}"))
    command.stamp(config, "d8e9f0a1b2c3")

    command.upgrade(config, "e0f1a2b3c4d5")

    table_names = set(inspect(engine).get_table_names())
    assert {
        "coding_run",
        "coding_candidate",
        "coding_agent_task",
        "coding_budget_ledger",
        "coding_iteration_reservation",
        "coding_run_event",
        "coding_validation_evidence",
        "coding_validation_report",
        "coding_run_outbox",
    } <= table_names
    run_columns = {column["name"] for column in inspect(engine).get_columns("coding_run")}
    assert {
        "workspace_id",
        "owner_user_id",
        "idempotency_key",
        "input_digest",
        "principal_json",
        "policy_snapshot_json",
        "environment_snapshot_json",
        "deadline",
    } <= run_columns
    task_columns = {column["name"] for column in inspect(engine).get_columns("coding_agent_task")}
    assert {"skill_version_bindings", "skill_read_addresses", "version"} <= task_columns
    evidence_columns = {
        column["name"] for column in inspect(engine).get_columns("coding_validation_evidence")
    }
    assert {"candidate_digest", "exit_code", "duration_ms", "artifact_id"} <= evidence_columns

    command.downgrade(config, "d8e9f0a1b2c3")

    assert not {
        "coding_run",
        "coding_candidate",
        "coding_agent_task",
        "coding_budget_ledger",
        "coding_iteration_reservation",
        "coding_run_event",
        "coding_validation_evidence",
        "coding_validation_report",
        "coding_run_outbox",
    } & set(inspect(engine).get_table_names())


def test_get_alembic_config_uses_backend_root_and_env_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://local-user:local-pass@localhost:5432/rhetorica",
    )

    config = get_alembic_config()
    alembic_ini = Path(config.config_file_name).resolve()

    assert alembic_ini.name == "alembic.ini"
    assert alembic_ini.parent.name == "backend"
    assert (
        Path(config.get_main_option("script_location")).resolve() == alembic_ini.parent / "alembic"
    )
    assert Path(config.get_main_option("prepend_sys_path")).resolve() == alembic_ini.parent / "src"
    assert (
        config.get_main_option("sqlalchemy.url")
        == "postgresql+psycopg://local-user:local-pass@localhost:5432/rhetorica"
    )


def test_has_managed_schema_is_false_for_fresh_database(tmp_path: Path) -> None:
    config = _sqlite_config(tmp_path)

    assert _has_managed_schema(config) is False


def test_has_managed_schema_is_true_for_legacy_schema_without_alembic_state(tmp_path: Path) -> None:
    config = _sqlite_config(tmp_path)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    Base.metadata.tables["user_account"].create(engine)

    assert _has_managed_schema(config) is True


def test_chat_token_usage_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'token_usage_migration.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE chat_conversation (id VARCHAR(36) PRIMARY KEY)"))

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "8f901a2b3c4d")
    command.upgrade(config, "9a0b1c2d3e4f")

    upgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_conversation")}
    upgraded_checks = {
        item["name"] for item in inspect(engine).get_check_constraints("chat_conversation")
    }
    assert {"llm_input_tokens", "llm_output_tokens"} <= upgraded_columns
    assert {
        "ck_chat_conversation_llm_input_tokens_non_negative",
        "ck_chat_conversation_llm_output_tokens_non_negative",
    } <= upgraded_checks

    command.downgrade(config, "8f901a2b3c4d")

    downgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_conversation")}
    assert "llm_input_tokens" not in downgraded_columns
    assert "llm_output_tokens" not in downgraded_columns


def test_chat_message_source_provenance_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'source_provenance_migration.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE chat_message ("
                "id VARCHAR(36) PRIMARY KEY, "
                "conversation_id VARCHAR(36) NOT NULL"
                ")"
            )
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "9a0b1c2d3e4f")
    command.upgrade(config, "a0b1c2d3e4f5")

    upgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_message")}
    upgraded_indexes = {item["name"] for item in inspect(engine).get_indexes("chat_message")}
    assert {"source_provider", "source_message_id"} <= upgraded_columns
    assert "ix_chat_message_conversation_source" in upgraded_indexes

    command.downgrade(config, "9a0b1c2d3e4f")

    downgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_message")}
    assert "source_provider" not in downgraded_columns
    assert "source_message_id" not in downgraded_columns


def test_chat_cache_usage_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'cache_usage_migration.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE chat_conversation ("
                "id VARCHAR(36) PRIMARY KEY, "
                "llm_input_tokens BIGINT NOT NULL DEFAULT 0, "
                "llm_output_tokens BIGINT NOT NULL DEFAULT 0"
                ")"
            )
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "a0b1c2d3e4f5")
    command.upgrade(config, "c2d3e4f5a6b7")

    upgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_conversation")}
    assert {
        "llm_cached_input_tokens",
        "llm_cache_write_input_tokens",
        "llm_cache_usage_reported_calls",
    } <= upgraded_columns
    upgraded_checks = {
        item["name"] for item in inspect(engine).get_check_constraints("chat_conversation")
    }
    expected_checks = {
        "ck_chat_conv_cached_input_nonneg",
        "ck_chat_conv_cache_write_nonneg",
        "ck_chat_conv_cache_reported_nonneg",
    }
    assert expected_checks <= upgraded_checks
    assert all(len(name) <= 63 for name in expected_checks)

    command.downgrade(config, "a0b1c2d3e4f5")
    downgraded_columns = {item["name"] for item in inspect(engine).get_columns("chat_conversation")}
    assert "llm_cached_input_tokens" not in downgraded_columns
    assert "llm_cache_write_input_tokens" not in downgraded_columns
    assert "llm_cache_usage_reported_calls" not in downgraded_columns


def test_llm_credential_binding_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'llm_credential_binding.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    engine = runtime.engine

    # Build the immediately preceding schema directly: historical migrations
    # before e7f8a9b0c1d2 include PostgreSQL-only JSON casts and are covered by
    # their own tests.  This isolates the new revision's real upgrade contract.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE chat_llm_credential_binding"))
        connection.execute(
            text("ALTER TABLE llm_connector_config DROP COLUMN credential_set_version")
        )
    command.stamp(config, "e7f8a9b0c1d2")
    command.upgrade(config, "a1b2c3d4e5f6")

    inspector = inspect(engine)
    assert "chat_llm_credential_binding" in inspector.get_table_names()
    connector_columns = {column["name"] for column in inspector.get_columns("llm_connector_config")}
    binding_columns = {
        column["name"] for column in inspector.get_columns("chat_llm_credential_binding")
    }
    assert "credential_set_version" in connector_columns
    assert {
        "conversation_id",
        "llm_connector_id",
        "credential_index",
        "credential_set_version",
        "created_at",
        "updated_at",
    } <= binding_columns
    assert {
        "chat_conversation",
        "llm_connector_config",
    } <= {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("chat_llm_credential_binding")
    }

    command.downgrade(config, "e7f8a9b0c1d2")
    inspector = inspect(engine)
    assert "chat_llm_credential_binding" not in inspector.get_table_names()
    assert "credential_set_version" not in {
        column["name"] for column in inspector.get_columns("llm_connector_config")
    }


def test_user_api_rate_limit_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'user_api_rate_limit.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    engine = runtime.engine

    command.stamp(config, "a1b2c3d4e5f7")
    command.downgrade(config, "a1b2c3d4e5f6")
    assert "api_rate_limit_per_hour" not in {
        column["name"] for column in inspect(engine).get_columns("user_account")
    }
    command.upgrade(config, "a1b2c3d4e5f7")

    inspector = inspect(engine)
    assert "api_rate_limit_per_hour" in {
        column["name"] for column in inspector.get_columns("user_account")
    }
    assert "ck_user_account_api_rate_limit_positive" in {
        constraint["name"] for constraint in inspector.get_check_constraints("user_account")
    }

    command.downgrade(config, "a1b2c3d4e5f6")
    assert "api_rate_limit_per_hour" not in {
        column["name"] for column in inspect(engine).get_columns("user_account")
    }


def test_connector_extra_credentials_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'connector_extra_credentials.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE connector_config (id VARCHAR(36) PRIMARY KEY)"))

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "d3e4f5a6b7c8")
    command.upgrade(config, "4f6a8c0e2b1d")

    upgraded_columns = {item["name"] for item in inspect(engine).get_columns("connector_config")}
    assert "extra_credential_secret_refs" in upgraded_columns

    command.downgrade(config, "d3e4f5a6b7c8")
    downgraded_columns = {item["name"] for item in inspect(engine).get_columns("connector_config")}
    assert "extra_credential_secret_refs" not in downgraded_columns


def test_workspace_migration_backfills_memberships_without_changing_resource_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'workspace_migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    users = UserAccountRepository(runtime)
    admin, _ = users.ensure_local_admin(
        workspace_id="legacy",
        login="admin",
        password_hash="hashed",
    )
    analyst = users.create_local_user(
        workspace_id="legacy",
        login="analyst",
        password_hash="hashed",
        role="analyst",
    )
    connector = ConnectorRepository(runtime, secret_store=InMemorySecretStore()).upsert(
        UpsertConnectorRequest(
            name="trino",
            workspace_id="legacy",
            connection=EngineConnectionConfig(
                engine="trino",
                host="trino.internal",
                port=8443,
                username="legacy",
                password="secret",
            ),
        )
    )
    with runtime.engine.begin() as connection:
        original_secret_ref = connection.execute(
            text("SELECT password_secret_ref FROM connector_config WHERE id = :id"),
            {"id": connector.id},
        ).scalar_one()
        connection.execute(text("DROP TABLE workspace_policy_audit_event"))
        connection.execute(text("DROP TABLE workspace_resource_grant"))
        connection.execute(text("DROP TABLE workspace_membership"))
        connection.execute(text("DROP TABLE workspace"))

    command.stamp(config, "e8f9a0b1c2d3")
    command.upgrade(config, "f0a1b2c3d4e5")
    command.upgrade(config, "f0a1b2c3d4e5")

    with runtime.engine.connect() as connection:
        memberships = connection.execute(
            text(
                "SELECT user_id, role FROM workspace_membership "
                "WHERE workspace_id = 'legacy' ORDER BY role"
            )
        ).all()
        current_connector = connection.execute(
            text("SELECT id, password_secret_ref FROM connector_config WHERE id = :id"),
            {"id": connector.id},
        ).one()
    assert memberships == [(analyst.id, "analyst"), (admin.id, "owner")]
    assert current_connector == (connector.id, original_secret_ref)


def test_default_workspace_membership_migration_restores_minimum_analyst_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'default_workspace_membership.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_DEFAULT_WORKSPACE_ID", "default")
    config = get_alembic_config()
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    users = UserAccountRepository(runtime)
    first = users.create_local_user(
        workspace_id="legacy",
        login="first",
        password_hash="hashed",
        role="analyst",
    )
    second = users.create_local_user(
        workspace_id="legacy",
        login="second",
        password_hash="hashed",
        role="analyst",
    )
    now = datetime.now(tz=UTC)
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO workspace "
                "(id, name, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('default', 'Default', 0, '{}', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO workspace_membership "
                "(workspace_id, user_id, role, is_active, created_at, updated_at) "
                "VALUES ('default', :user_id, 'viewer', 0, :now, :now)"
            ),
            {"user_id": first.id, "now": now},
        )

    command.stamp(config, "f0a1b2c3d4e5")
    command.upgrade(config, "f1b2c3d4e5f6")

    with runtime.engine.connect() as connection:
        workspace_active = connection.execute(
            text("SELECT is_active FROM workspace WHERE id = 'default'")
        ).scalar_one()
        memberships = connection.execute(
            text(
                "SELECT user_id, role, is_active FROM workspace_membership "
                "WHERE workspace_id = 'default' ORDER BY user_id"
            )
        ).all()
    assert bool(workspace_active) is True
    assert memberships == sorted([
        (first.id, "analyst", True),
        (second.id, "analyst", True),
    ])


def test_local_identity_workspace_migration_preserves_home_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'local_identity_workspace.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AUTH_DEFAULT_WORKSPACE_ID", "default")
    config = get_alembic_config()
    runtime = DatabaseRuntime(database_url)
    runtime.create_schema()
    users = UserAccountRepository(runtime)
    legacy_user = users.create_local_user(
        workspace_id="legacy",
        login="legacy-user",
        password_hash="hashed",
        role="analyst",
    )

    command.stamp(config, "f0a1b2c3d4e5")
    command.upgrade(config, "f2c3d4e5f6a7")

    persisted = users.get_by_login(workspace_id="legacy", login="legacy-user")
    assert persisted is not None
    assert persisted.id == legacy_user.id
    assert users.get_by_login(workspace_id="default", login="legacy-user") is None
    with runtime.engine.connect() as connection:
        workspace_id = connection.execute(
            text("SELECT workspace_id FROM user_account WHERE id = :user_id"),
            {"user_id": legacy_user.id},
        ).scalar_one()
    assert workspace_id == "legacy"


def test_legacy_connector_cleanup_migration_removes_tables_columns_and_mapping_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy_connector_cleanup.db'}"
    engine = create_engine(database_url)
    legacy_tables = {
        "metadata_table_usage",
        "openmetadata_connector_config",
        "confluence_connector_config",
        "jira_connector_config",
    }
    legacy_columns = {
        "allowed_metadata_connector_names",
        "allowed_confluence_connector_names",
        "allowed_jira_connector_names",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE agent_profile ("
                "id VARCHAR(36) PRIMARY KEY, "
                "allowed_metadata_connector_names JSON NOT NULL DEFAULT '[]', "
                "allowed_confluence_connector_names JSON NOT NULL DEFAULT '[]', "
                "allowed_jira_connector_names JSON NOT NULL DEFAULT '[]'"
                ")"
            )
        )
        for table_name in legacy_tables:
            connection.execute(text(f"CREATE TABLE {table_name} (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE connector_config ("
                "id VARCHAR(36) PRIMARY KEY, extra_settings JSON NOT NULL DEFAULT '{}'"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO connector_config (id, extra_settings) "
                "VALUES (:connector_id, :settings)"
            ),
            {
                "connector_id": "db-1",
                "settings": json.dumps({
                    "metadata_service": "catalog",
                    "metadata_database": "analytics",
                    "keep": True,
                }),
            },
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "4f6a8c0e2b1d")
    command.upgrade(config, "5a7c9e1f3b2d")

    inspector = inspect(engine)
    assert legacy_tables.isdisjoint(inspector.get_table_names())
    upgraded_columns = {item["name"] for item in inspector.get_columns("agent_profile")}
    assert legacy_columns.isdisjoint(upgraded_columns)
    with engine.connect() as connection:
        settings = connection.execute(
            text("SELECT extra_settings FROM connector_config WHERE id = 'db-1'")
        ).scalar_one()
    assert settings == '{"keep":true}'


def test_legacy_agent_settings_migration_upgrades_and_downgrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy_agent_settings.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE agent_profile ("
                "id VARCHAR(36) PRIMARY KEY, settings JSON NOT NULL DEFAULT '{}'"
                ")"
            )
        )
        connection.execute(
            text("INSERT INTO agent_profile (id, settings) VALUES (:id, :settings)"),
            {
                "id": "agent-1",
                "settings": json.dumps({
                    "run_sql_allowed": True,
                    "jira_write_allowed": False,
                    "temperature": 0.3,
                }),
            },
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "5a7c9e1f3b2d")
    command.upgrade(config, "6b8d0f2a4c3e")

    with engine.connect() as connection:
        upgraded = json.loads(
            connection.execute(
                text("SELECT settings FROM agent_profile WHERE id = 'agent-1'")
            ).scalar_one()
        )
    assert upgraded == {"run_sql_allowed": True, "temperature": 0.3}

    command.downgrade(config, "5a7c9e1f3b2d")
    with engine.connect() as connection:
        downgraded = json.loads(
            connection.execute(
                text("SELECT settings FROM agent_profile WHERE id = 'agent-1'")
            ).scalar_one()
        )
    assert downgraded == {
        "run_sql_allowed": True,
        "temperature": 0.3,
        "jira_write_allowed": False,
    }


def test_llm_top_p_migration_preserves_rows(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'llm_top_p.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE llm_connector_config (id TEXT PRIMARY KEY, model TEXT)")
        )
        connection.execute(text("INSERT INTO llm_connector_config VALUES ('existing', 'test')"))
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "c5d6e7f8a9b0")
    command.upgrade(config, "d6e7f8a9b0c1")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT top_p FROM llm_connector_config")).scalar() is None
    assert "ck_llm_connector_top_p" in {
        c["name"] for c in inspect(engine).get_check_constraints("llm_connector_config")
    }
    command.downgrade(config, "c5d6e7f8a9b0")
    assert "top_p" not in {c["name"] for c in inspect(engine).get_columns("llm_connector_config")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT model FROM llm_connector_config")).scalar() == "test"


def test_agent_policy_migration_backfills_closed_policies_and_downgrades(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent_policy.db'}"
    engine = create_engine(database_url)
    legacy_settings = {"run_sql_allowed": True, "temperature": 0.2}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user_account (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE workspace (id VARCHAR(128) PRIMARY KEY, name VARCHAR(255))")
        )
        connection.execute(
            text(
                "CREATE TABLE agent_profile ("
                "id VARCHAR(36) PRIMARY KEY, workspace_id VARCHAR(128) NOT NULL, "
                "created_by VARCHAR(36), settings JSON NOT NULL)"
            )
        )
        connection.execute(text("INSERT INTO workspace VALUES ('analytics', 'Analytics')"))
        connection.execute(
            text(
                "INSERT INTO agent_profile (id, workspace_id, created_by, settings) "
                "VALUES ('agent-1', 'analytics', NULL, :settings)"
            ),
            {"settings": json.dumps(legacy_settings)},
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "a5c6d7e8f901")
    command.upgrade(config, "f5c6d7e8f9a0")
    command.upgrade(config, "f5c6d7e8f9a0")

    inspector = inspect(engine)
    assert {"agent_governed_policy", "default_assistant_policy"} <= set(inspector.get_table_names())
    with engine.connect() as connection:
        policy = connection.execute(
            text("SELECT revision, policy_json FROM agent_governed_policy")
        ).one()
        default_policy = connection.execute(
            text("SELECT revision, policy_json FROM default_assistant_policy")
        ).one()
        unchanged_settings = json.loads(
            connection.execute(text("SELECT settings FROM agent_profile")).scalar_one()
        )
    assert policy.revision == 1
    assert json.loads(policy.policy_json)["gitlab_write_scopes"] == []
    assert default_policy.revision == 1
    assert json.loads(default_policy.policy_json)["delegation_grants"] == []
    assert unchanged_settings == legacy_settings

    command.downgrade(config, "a5c6d7e8f901")
    assert {"agent_governed_policy", "default_assistant_policy"}.isdisjoint(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert (
            json.loads(connection.execute(text("SELECT settings FROM agent_profile")).scalar_one())
            == legacy_settings
        )


def test_coding_team_backfill_creates_default_team_for_legacy_presets(
    tmp_path: Path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'coding_teams.db'}"
    engine = create_engine(database_url)
    legacy = {
        "root_agent_id": "coordinator",
        "developer_agent_id": "developer",
        "qa_agent_id": "qa",
        "gitlab_connector_id": "gitlab",
        "project_id": "group/project",
        "target_branch": "main",
        "environment_profile_id": "env",
    }
    invalid = {**legacy, "qa_agent_id": "developer"}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE coding_agent_task (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE workspace_coding_defaults (workspace_id VARCHAR(128) PRIMARY KEY, "
                "revision INTEGER, resources_json JSON, updated_by VARCHAR(36), "
                "updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE coding_agent_team (id VARCHAR(36) PRIMARY KEY, "
                "workspace_id VARCHAR(128), name VARCHAR(120), active_revision_id VARCHAR(36), "
                "latest_revision INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE coding_agent_team_revision (id VARCHAR(36) PRIMARY KEY, "
                "workspace_id VARCHAR(128), team_id VARCHAR(36), revision INTEGER, "
                "coordinator_agent_profile_id VARCHAR(36), coordinator_instruction VARCHAR(12000), "
                "members_json JSON, is_active BOOLEAN, created_by VARCHAR(36), created_at DATETIME)"
            )
        )
        for workspace_id, resources in [
            ("analytics", legacy),
            ("broken", invalid),
            ("empty", None),
        ]:
            connection.execute(
                text(
                    "INSERT INTO workspace_coding_defaults VALUES "
                    "(:workspace_id, 1, :resources, 'owner', '2026-01-01')"
                ),
                {
                    "workspace_id": workspace_id,
                    "resources": json.dumps(resources) if resources else None,
                },
            )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = get_alembic_config()
    command.stamp(config, "5bc6d7e8f9a0")
    command.upgrade(config, "6cd7e8f9a0b1")

    with engine.connect() as connection:
        team = connection.execute(
            text("SELECT id, name, active_revision_id FROM coding_agent_team")
        ).one()
        revision = connection.execute(
            text(
                "SELECT id, coordinator_agent_profile_id, members_json, is_active "
                "FROM coding_agent_team_revision"
            )
        ).one()
        presets = {
            row.workspace_id: json.loads(row.resources_json) if row.resources_json else None
            for row in connection.execute(
                text("SELECT workspace_id, resources_json FROM workspace_coding_defaults")
            )
        }
    members = json.loads(revision.members_json)
    assert team.name == "Default coding team"
    assert team.active_revision_id == revision.id and revision.is_active
    assert revision.coordinator_agent_profile_id == "coordinator"
    assert [(m["agent_profile_id"], m["capability"]) for m in members] == [
        ("developer", "candidate"),
        ("qa", "review"),
    ]
    assert presets["analytics"] == {**legacy, "coding_team_id": team.id}
    assert presets["broken"] == invalid
    assert presets["empty"] is None
    assert {"team_member_id", "team_member_label"} <= {
        column["name"] for column in inspect(engine).get_columns("coding_agent_task")
    }

    command.downgrade(config, "5bc6d7e8f9a0")
    with engine.connect() as connection:
        restored = json.loads(
            connection.execute(
                text(
                    "SELECT resources_json FROM workspace_coding_defaults "
                    "WHERE workspace_id = 'analytics'"
                )
            ).scalar_one()
        )
    assert restored == legacy
    assert "team_member_id" not in {
        column["name"] for column in inspect(engine).get_columns("coding_agent_task")
    }
