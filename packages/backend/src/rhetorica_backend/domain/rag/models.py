from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RagPipelineTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=36)
    message_id: str = Field(min_length=1, max_length=36)
    rag_connector_name: str | None = Field(default=None, min_length=1, max_length=128)


class RagGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_store: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2000)
    compact_summary: str = Field(min_length=1, max_length=4000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    article_markdown: str | None = Field(default=None, max_length=12000)


class RagPipelineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["stored", "skipped"]
    reason: str = Field(min_length=1, max_length=1024)
    point_id: str | None = None


class RagMergeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_update_existing: bool
    reason: str = Field(min_length=1, max_length=2000)
    updated_summary: str = Field(min_length=1, max_length=4000)
    updated_keywords: list[str] = Field(default_factory=list, max_length=20)
    updated_article_markdown: str | None = Field(default=None, max_length=12000)
