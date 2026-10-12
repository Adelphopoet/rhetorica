from __future__ import annotations

from rhetorica_backend.infra.postgres import chat_models as _chat_models  # noqa: F401
from rhetorica_backend.infra.postgres import file_models as _file_models  # noqa: F401
from rhetorica_backend.infra.postgres import models as _models  # noqa: F401
from rhetorica_backend.infra.postgres import scenario_models as _scenario_models  # noqa: F401
from rhetorica_backend.infra.postgres import (
    scenario_workflow_models as _scenario_workflow_models,  # noqa: F401
)
from rhetorica_backend.infra.postgres.base import Base
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(database_url, future=True, connect_args={"check_same_thread": False})
    return create_engine(database_url, future=True, pool_pre_ping=True)


class DatabaseRuntime:
    """Small SQLAlchemy runtime wrapper used by backend services."""

    def __init__(self, database_url: str):
        self.engine = build_engine(database_url)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def session(self) -> Session:
        return self._session_factory()

    def create_schema(self) -> None:
        """Create all database tables."""
        Base.metadata.create_all(self.engine)
