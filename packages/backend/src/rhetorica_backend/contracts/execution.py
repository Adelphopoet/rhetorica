from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rhetorica_backend.sql_guard.models import SqlGuardPolicy, SqlGuardViolation

EngineType = Literal["clickhouse", "trino", "greenplum", "impala"]
EngineScalarSetting = str | int | float | bool
EngineExtraSetting = EngineScalarSetting | dict[str, EngineScalarSetting]
EngineExtraCredentialValue = Annotated[str, Field(min_length=1, max_length=4096)] | None
ExecutionStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancel_requested",
    "cancelled",
]


class EngineConnectionConfig(BaseModel):
    """Connection details for query execution engines."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    engine: EngineType
    host: str
    port: int = Field(ge=1, le=65535)
    username: str
    password: str | None = None
    usage_description: str | None = Field(default=None, max_length=1000)
    database: str | None = None
    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    secure: bool = False
    verify: bool = True
    extra_settings: dict[str, EngineExtraSetting] = Field(default_factory=dict)
    extra_credentials: dict[str, EngineExtraCredentialValue] | None = Field(
        default=None,
        max_length=50,
    )

    @field_validator("extra_credentials")
    @classmethod
    def _normalize_extra_credentials(
        cls,
        value: dict[str, EngineExtraCredentialValue] | None,
    ) -> dict[str, EngineExtraCredentialValue] | None:
        if value is None:
            return None
        normalized: dict[str, EngineExtraCredentialValue] = {}
        for raw_name, secret in value.items():
            name = raw_name.strip()
            if not name or len(name) > 255:
                raise ValueError("extra_credential_name_invalid")
            if name in normalized:
                raise ValueError("extra_credential_name_duplicate")
            normalized[name] = secret
        return normalized

    @model_validator(mode="after")
    def _extra_credentials_require_trino(self) -> EngineConnectionConfig:
        if self.engine != "trino" and self.extra_credentials:
            raise ValueError("extra_credentials_require_trino")
        return self

    def redacted_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", by_alias=True)
        data.pop("password", None)
        if data.get("extra_credentials") is not None:
            data["extra_credentials"] = dict.fromkeys(data["extra_credentials"])
        return data


def sql_dialect_for_engine(engine: str) -> str:
    normalized = engine.strip().lower()
    if normalized == "greenplum":
        return "postgres"
    if normalized == "impala":
        return "hive"
    return normalized


class ExecuteSqlRequest(BaseModel):
    """Execution request for guarded read-only SQL."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    connection: EngineConnectionConfig
    dialect: str | None = None
    policy: SqlGuardPolicy = Field(default_factory=SqlGuardPolicy)
    workspace_id: str = Field(default="default", min_length=1)
    actor_id: str | None = None
    conversation_id: str | None = None


class ExternalSqlExecutionRequest(BaseModel):
    """External execution request that resolves connector via workspace governance.

    This contract is used by public API routes. It does NOT accept raw connection
    config to prevent bypassing the governed connector policy.
    """

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    connector_name: str | None = None
    workspace_id: str = Field(default="default", min_length=1)
    dialect: str | None = None
    policy: SqlGuardPolicy = Field(default_factory=SqlGuardPolicy)
    actor_id: str | None = None


class QueryPreview(BaseModel):
    """Small query payload returned to API clients."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)


class ExecuteSqlResponse(BaseModel):
    """Execution result response."""

    execution_id: str
    status: ExecutionStatus
    engine: EngineType
    query_id: str | None = None
    normalized_sql: str | None = None
    applied_row_limit: int | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)
    violations: list[SqlGuardViolation] = Field(default_factory=list)
    preview: QueryPreview = Field(default_factory=QueryPreview)
    error_message: str | None = None


class CancelExecutionResponse(BaseModel):
    """Cancel request result."""

    execution_id: str
    cancelled: bool
    status: ExecutionStatus
    message: str


class ExecutionHistoryItem(BaseModel):
    """Persisted query execution metadata."""

    execution_id: str
    workspace_id: str
    actor_id: str | None = None
    engine: EngineType
    status: ExecutionStatus
    submitted_sql: str
    normalized_sql: str | None
    timeout_ms: int
    row_limit: int
    row_count: int | None
    duration_ms: int | None
    query_id: str | None
    error_message: str | None
    warnings: list[str] = Field(default_factory=list)
    violations: list[SqlGuardViolation] = Field(default_factory=list)
    preview: QueryPreview = Field(default_factory=QueryPreview)
    connection_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None


class ExecutionDetailResponse(BaseModel):
    """Execution detail assembled from persisted history and task result payload."""

    history: ExecutionHistoryItem
    result: ExecuteSqlResponse | None = None
