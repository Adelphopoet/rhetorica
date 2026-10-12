from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentationPageDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_ref: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    source_path: str | None = None
    markdown_url: str | None = None
    summary: str | None = None
    body_text: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class DocumentationSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    pages_seen: int = 0
    pages_updated: int = 0
    chunks_indexed: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
