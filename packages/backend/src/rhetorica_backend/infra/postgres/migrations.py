"""Alembic migrations runner for the Rhetorica backend."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from rhetorica_backend.infra.postgres.base import Base


def _find_backend_root() -> Path:
    """Locate the backend package root from local or container execution."""
    current_file = Path(__file__).resolve()

    for parent in current_file.parents:
        if (parent / "alembic.ini").exists():
            return parent

    fallback_root = Path("/workspace/packages/backend")
    if (fallback_root / "alembic.ini").exists():
        return fallback_root

    searched_paths = ", ".join(str(parent / "alembic.ini") for parent in current_file.parents)
    raise FileNotFoundError(searched_paths)


def get_alembic_config() -> Config:
    """Get Alembic config with proper path resolution.

    Works from both Docker containers and local development.
    """
    backend_root = _find_backend_root()
    alembic_ini = backend_root / "alembic.ini"

    config = Config(str(alembic_ini))

    # Set script_location relative to the config file
    config.set_main_option(
        "script_location",
        str(alembic_ini.parent / "alembic"),
    )

    # Ensure the src directory is in the path for imports
    config.set_main_option(
        "prepend_sys_path",
        str(backend_root / "src"),
    )

    # Override sqlalchemy.url with environment variable if available
    # This allows Docker containers to use the correct database host
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        config.set_main_option("sqlalchemy.url", db_url)

    return config


def _alembic_version_table_exists(config: Config) -> bool:
    """Check if alembic_version table exists."""
    from sqlalchemy import create_engine, text

    db_url = config.get_main_option("sqlalchemy.url")
    if not db_url:
        return False

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'alembic_version'
                    )
                """)
            )
            return result.scalar()
    except Exception:
        return False


def _has_managed_schema(config: Config) -> bool:
    """Check whether application tables already exist without Alembic state."""
    from sqlalchemy import create_engine, inspect

    db_url = config.get_main_option("sqlalchemy.url")
    if not db_url:
        return False

    managed_tables = set(Base.metadata.tables) - {"alembic_version"}
    if not managed_tables:
        return False

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            existing_tables = set(inspect(conn).get_table_names())
        return bool(existing_tables & managed_tables)
    except Exception:
        return False


def run_migrations() -> None:
    """Run all pending migrations."""
    config = get_alembic_config()

    # If application tables already exist but Alembic state is missing,
    # stamp first to preserve the legacy create_all bootstrap path.
    if not _alembic_version_table_exists(config) and _has_managed_schema(config):
        print("Alembic version table not found. Stamping with head revision...")
        command.stamp(config, "head")
        print("Database stamped. Running migrations...")

    command.upgrade(config, "head")


def downgrade_migrations(revision: str = "-1") -> None:
    """Downgrade migrations."""
    config = get_alembic_config()
    command.downgrade(config, revision)
