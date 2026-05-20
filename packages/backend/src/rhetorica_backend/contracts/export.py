from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from rhetorica_backend.contracts.analysis_document import AnalysisDocument
from rhetorica_backend.contracts.chat import AnswerClaim, AnswerEvidence, ClaimEvidenceLink
from rhetorica_backend.contracts.execution import QueryPreview
from rhetorica_backend.contracts.visualization import VisualizationSpec


class AnalysisExportFormat(str, Enum):
    """Supported export targets for immutable analysis snapshots."""

    HTML = "html"
    PDF = "pdf"
    IPYNB = "ipynb"


class AnalysisExecutionSummary(BaseModel):
    """Execution metadata included in the canonical export bundle."""

    execution_id: str | None = None
    status: str | None = None
    engine: str | None = None
    row_count: int | None = None
    duration_ms: int | None = None
    query_id: str | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AnalysisSourceMetadata(BaseModel):
    """Snapshot provenance for analyst-facing exports."""

    workspace_id: str
    conversation_id: str
    assistant_message_id: str
    message_created_at: datetime
    selected_database_connector: str | None = None
    metadata_sources: list[str] = Field(default_factory=list)
    context_tables: list[str] = Field(default_factory=list)


class AnalysisScenarioProvenance(BaseModel):
    """Scenario provenance placeholder until artifact library lands."""

    scenario_run_id: str | None = None
    scenario_id: str | None = None
    scenario_title: str | None = None


class AnalysisExportMetadata(BaseModel):
    """Metadata attached to a generated immutable export bundle."""

    bundle_version: str = "v1"
    generated_at: datetime
    source_kind: str = "chat_message_snapshot"


class AnalysisExportBundle(BaseModel):
    """Canonical export snapshot shared by all output formats."""

    question: str
    answer_markdown: str
    generated_sql: str | None = None
    execution_summary: AnalysisExecutionSummary = Field(default_factory=AnalysisExecutionSummary)
    preview: QueryPreview = Field(default_factory=QueryPreview)
    analysis_document: AnalysisDocument | None = None
    visualization: VisualizationSpec | None = None
    claims: list[AnswerClaim] = Field(default_factory=list)
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    source_metadata: AnalysisSourceMetadata
    scenario_provenance: AnalysisScenarioProvenance = Field(
        default_factory=AnalysisScenarioProvenance
    )
    export_metadata: AnalysisExportMetadata
