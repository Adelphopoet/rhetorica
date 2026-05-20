from pathlib import Path

from alembic.config import Config
from rhetorica_backend.infra.postgres import chat_models as _chat_models  # noqa: F401
from rhetorica_backend.infra.postgres import models as _models  # noqa: F401
from rhetorica_backend.infra.postgres import scenario_models as _scenario_models  # noqa: F401
from rhetorica_backend.infra.postgres.base import Base
from rhetorica_backend.infra.postgres.migrations import _has_managed_schema, get_alembic_config


def _sqlite_config(tmp_path: Path) -> Config:
    config = Config()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'test.db'}")
    return config


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
    from sqlalchemy import create_engine

    config = _sqlite_config(tmp_path)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    Base.metadata.tables["user_account"].create(engine)

    assert _has_managed_schema(config) is True
