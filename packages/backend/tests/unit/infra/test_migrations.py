import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from rhetorica_backend.infra.postgres import chat_models as _chat_models  # noqa: F401
from rhetorica_backend.infra.postgres import models as _models  # noqa: F401
from rhetorica_backend.infra.postgres import scenario_models as _scenario_models  # noqa: F401
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.migrations import _has_managed_schema, get_alembic_config
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from sqlalchemy import create_engine, inspect, text


def _sqlite_config(tmp_path: Path) -> Config:
    config = Config()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'test.db'}")
    return config


def test_alembic_revision_graph_has_single_head() -> None:
    heads = ScriptDirectory.from_config(get_alembic_config()).get_heads()

    assert len(heads) == 1, f"Expected one Alembic head, found: {heads}"


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
    connector_columns = {
        column["name"] for column in inspector.get_columns("llm_connector_config")
    }
    binding_columns = {
        column["name"]
        for column in inspector.get_columns("chat_llm_credential_binding")
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
        connection.execute(text("CREATE TABLE llm_connector_config (id TEXT PRIMARY KEY, model TEXT)"))
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
