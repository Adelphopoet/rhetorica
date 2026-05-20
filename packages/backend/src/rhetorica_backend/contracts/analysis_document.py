from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from rhetorica_backend.contracts.execution import QueryPreview
from rhetorica_backend.contracts.visualization import VisualizationSpec


class AnalysisDocumentProvenance(BaseModel):
    """Structured provenance for analyst-facing report surface."""

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["chat_answer"] = "chat_answer"
    execution_id: str | None = None
    planner_mode: Literal["rule_fallback", "llm_validated", "mixed"] = "rule_fallback"


class AnalysisMetricItem(BaseModel):
    """One KPI-like metric cell inside a metric grid."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=120)
    context: str | None = Field(default=None, max_length=255)
    delta: str | None = Field(default=None, max_length=64)
    tone: Literal["neutral", "positive", "warning", "negative"] = "neutral"


class AnalysisSectionHeaderBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["section_header"] = "section_header"
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=400)


class AnalysisNarrativeNoteBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["narrative_note"] = "narrative_note"
    title: str | None = Field(default=None, max_length=160)
    markdown: str = Field(min_length=1)


class AnalysisFindingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["finding"] = "finding"
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1)
    emphasis: Literal["info", "important", "critical"] = "important"


class AnalysisCaveatBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["caveat"] = "caveat"
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1)


class AnalysisMetricGridBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["metric_grid"] = "metric_grid"
    title: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=400)
    items: list[AnalysisMetricItem] = Field(default_factory=list)


class AnalysisChartBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["chart"] = "chart"
    title: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=400)
    insight: str | None = Field(default=None, max_length=400)
    source_execution_id: str | None = Field(default=None, max_length=255)
    preview: QueryPreview | None = None
    visualization: VisualizationSpec


class AnalysisTableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"] = "table"
    title: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=400)
    source: Literal["execution_preview"] = "execution_preview"
    source_execution_id: str | None = Field(default=None, max_length=255)
    preview: QueryPreview | None = None
    columns: list[str] = Field(default_factory=list)
    preferred_hidden: bool = True
    action_label: str | None = Field(default="Show rows", max_length=80)


class AnalysisDividerBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["divider"] = "divider"


AnalysisBlock = Annotated[
    AnalysisSectionHeaderBlock
    | AnalysisNarrativeNoteBlock
    | AnalysisFindingBlock
    | AnalysisCaveatBlock
    | AnalysisMetricGridBlock
    | AnalysisChartBlock
    | AnalysisTableBlock
    | AnalysisDividerBlock,
    Field(discriminator="kind"),
]


class AnalysisDocument(BaseModel):
    """Canonical multi-block analyst payload."""

    model_config = ConfigDict(extra="forbid")

    document_version: Literal["v1"] = "v1"
    title: str | None = Field(default=None, max_length=255)
    executive_summary: str | None = None
    blocks: list[AnalysisBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: AnalysisDocumentProvenance = Field(
        default_factory=AnalysisDocumentProvenance
    )
