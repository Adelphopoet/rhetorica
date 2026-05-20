from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rhetorica_backend.sql_guard.models import SqlGuardDecision, SqlGuardPolicy


class SqlGuardRequest(BaseModel):
    """Request contract for guarded SQL checks."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    dialect: str = Field(default="clickhouse", min_length=1)
    engine: str | None = Field(default=None, min_length=1)
    policy: SqlGuardPolicy = Field(default_factory=SqlGuardPolicy)


class SqlGuardResponse(SqlGuardDecision):
    """Response contract for guarded SQL checks."""
