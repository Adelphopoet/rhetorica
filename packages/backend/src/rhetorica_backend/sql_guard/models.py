from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SQL_TIMEOUT_MS = 300_000


class SqlGuardPolicy(BaseModel):
    """Execution controls attached to each guarded SQL request."""

    model_config = ConfigDict(extra="forbid")

    row_limit: int = Field(default=5000, ge=1, le=1_000_000)
    timeout_ms: int = Field(default=DEFAULT_SQL_TIMEOUT_MS, ge=100, le=900_000)
    max_concurrency: int = Field(default=4, ge=1, le=100)


class SqlGuardViolation(BaseModel):
    """A single policy violation discovered during SQL guard checks."""

    code: str
    message: str


class SqlGuardDecision(BaseModel):
    """Decision payload returned by SQL guard checks."""

    allowed: bool
    dialect: str
    normalized_sql: str | None = None
    policy: SqlGuardPolicy
    applied_row_limit: int | None = None
    warnings: list[str] = Field(default_factory=list)
    violations: list[SqlGuardViolation] = Field(default_factory=list)
