"""Shared metadata domain models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetadataEntityReference:
    """Compact typed reference to a governance entity."""

    name: str
    type: str | None = None
    display_name: str | None = None
    fully_qualified_name: str | None = None


@dataclass(frozen=True)
class MetadataMetricReference:
    """Compact OpenMetadata metric reference."""

    name: str
    display_name: str | None = None
    fully_qualified_name: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class DataQualitySummary:
    """Read-only quality status summary for one metadata entity."""

    total: int = 0
    success: int = 0
    failed: int = 0
    aborted: int = 0
    last_status: str | None = None
    last_run_at: int | None = None
    failed_tests: tuple[str, ...] = ()

    @property
    def has_failures(self) -> bool:
        return self.failed > 0 or self.aborted > 0

    @property
    def status(self) -> str:
        if self.failed > 0:
            return "failed"
        if self.aborted > 0:
            return "aborted"
        if self.success > 0 and self.total == self.success:
            return "success"
        if self.total > 0:
            return "unknown"
        return "not_configured"

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "aborted": self.aborted,
            "last_status": self.last_status,
            "last_run_at": self.last_run_at,
            "failed_tests": list(self.failed_tests),
        }


@dataclass
class TableContext:
    """Context information about a table."""

    source: str
    fqn: str
    columns: list[str]
    column_types: dict[str, str] = field(default_factory=dict)
    column_descriptions: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    service: str | None = None
    database: str | None = None
    schema: str | None = None
    owners: list[str] = field(default_factory=list)
    owner_refs: list[MetadataEntityReference] = field(default_factory=list)
    owner_teams: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    domain_refs: list[MetadataEntityReference] = field(default_factory=list)
    glossary_terms: list[MetadataEntityReference] = field(default_factory=list)
    related_metrics: list[MetadataMetricReference] = field(default_factory=list)
    quality_summary: DataQualitySummary | None = None
    tier: str | None = None
    certification: str | None = None
    usage_count: int = 0
    relevance_score: float = 0.0
    matched_on: list[str] = field(default_factory=list)
    usage_frequency: float = 0.0


@dataclass
class MetadataCacheEntry:
    """In-memory metadata cache entry."""

    tables: list[TableContext]
    warnings: list[str]
    expires_at: float
    stale_until: float
