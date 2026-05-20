"""Governed metric registry contracts for business metric discovery and validation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from rhetorica_backend.contracts.metrics import MetricDefinition


@runtime_checkable
class MetricRepositoryProtocol(Protocol):
    """Protocol defining the contract for metric repository implementations.

    This protocol enables Dependency Inversion Principle compliance, allowing
    the MetricRegistryService to depend on this abstraction rather than storage-specific
    implementations.
    """

    def get_by_id(self, workspace_id: str, metric_id: str) -> MetricDefinition | None:
        """Retrieve a metric definition by its unique metric_id within a workspace."""
        ...

    def get_by_name(self, workspace_id: str, name: str) -> MetricDefinition | None:
        """Retrieve a metric definition by its name within a workspace (case-insensitive)."""
        ...

    def list_all(self, workspace_id: str) -> list[MetricDefinition]:
        """List all metric definitions for a workspace, ordered by name."""
        ...

    def upsert(self, metric: MetricDefinition) -> MetricDefinition:
        """Create or update a metric definition within a workspace."""
        ...

    def delete(self, workspace_id: str, metric_id: str) -> bool:
        """Delete a metric definition by its metric_id within a workspace."""
        ...


class MetricDefinition(BaseModel):
    """Governed metric definition used for semantic SQL planning and LLM discovery.

    A metric definition captures the authoritative definition of a business metric
    including its SQL formula, aggregation grain, allowed dimensions, and governance
    metadata. This enables the system to:
    - Discover metrics from natural language (e.g., "revenue", "GMV", "churn")
    - Validate SQL queries against metric contracts
    - Enforce allowed dimensions in BI queries
    - Provide certified metric lineage to analysts
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1, max_length=128, description="Unique metric identifier")
    name: str = Field(min_length=1, max_length=255, description="Human-readable metric name")
    description: str = Field(max_length=2000, description="Business description of the metric")
    formula: str = Field(max_length=65535, description="SQL expression/formula for the metric")
    grain: str = Field(min_length=1, max_length=64, description="Aggregation grain (e.g., daily, monthly)")
    allowed_dimensions: list[str] = Field(
        default_factory=list,
        description="Allowed dimension columns for slicing this metric",
    )
    default_filters: dict[str, str] = Field(
        default_factory=dict,
        description="Default WHERE filters to apply when using this metric",
    )
    owner: str | None = Field(
        default=None,
        max_length=128,
        description="Domain/team owner responsible for this metric",
    )
    certified: bool = Field(
        default=False,
        description="Whether certified by data governance",
    )
    source_tables: list[str] = Field(
        default_factory=list,
        description="Underlying source table FQNs",
    )
    join_policy: str | None = Field(
        default=None,
        max_length=64,
        description="How to join source tables (e.g., inner, left, star)",
    )
    freshness_hours: float | None = Field(
        default=None,
        ge=0,
        description="SLA freshness hint in hours",
    )
    workspace_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class MetricCompactCard(BaseModel):
    """Stripped-down metric context for LLM prompt injection.

    This card provides only essential information needed for SQL planning
    while minimizing context window usage. The full formula is truncated
    to 200 characters to prevent context overflow.
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    name: str
    formula_summary: str = Field(max_length=200, description="First 200 chars of formula")
    grain: str
    allowed_dimensions: list[str]
    source_refs: list[str]
    certified: bool


class MetricPreflightError(BaseModel):
    """Structured error from metric consistency preflight check."""

    code: str
    message: str
    identifier: str | None = None
    table: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class MetricPreflightResult(BaseModel):
    """Result payload for metric consistency preflight."""

    status: str = Field(pattern="^(pass|fail)$")
    errors: list[MetricPreflightError] = Field(default_factory=list)
