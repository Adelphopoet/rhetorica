from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from rhetorica_backend.contracts.analysis_document import AnalysisChartBlock, AnalysisDocument
from rhetorica_backend.contracts.chat import ChatAskResponse
from rhetorica_backend.contracts.execution import (
    ExecuteSqlResponse,
    ExecutionDetailResponse,
    QueryPreview,
)
from rhetorica_backend.contracts.export import (
    AnalysisExecutionSummary,
    AnalysisExportBundle,
    AnalysisExportFormat,
    AnalysisExportMetadata,
    AnalysisScenarioProvenance,
    AnalysisSourceMetadata,
)
from rhetorica_backend.contracts.visualization import VisualizationKind, VisualizationSpec
from rhetorica_backend.domain.results.analysis_document_builder import (
    primary_visualization_from_document,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner
from rhetorica_backend.infra.postgres.chat_repository import ChatMessageItem


@dataclass(frozen=True)
class RenderedAnalysisExport:
    """Rendered export payload ready for download."""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class _ChartSeries:
    name: str
    values: list[float | None]
    color: str


@dataclass(frozen=True)
class _CartesianChartSnapshot:
    title: str
    summary: str | None
    x_axis_label: str | None
    y_axis_label: str | None
    labels: list[str]
    series: list[_ChartSeries]
    mode: str
    stacked: bool


class AnalysisExportService:
    """Builds immutable analysis bundles and renders them into delivery formats."""

    _MAX_TABLE_ROWS = 12
    _HTML_CSS = """
    :root {
      color-scheme: light;
      --bg: #f3f6fb;
      --panel: #ffffff;
      --ink: #182433;
      --muted: #5d6b82;
      --line: #d8e1ef;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --success: #0f766e;
      --warning: #b45309;
      --danger: #b91c1c;
      font-family: "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #eef3fb 0%, #f8fbff 100%);
      color: var(--ink);
      padding: 32px;
    }
    .report {
      max-width: 1080px;
      margin: 0 auto;
      display: grid;
      gap: 20px;
    }
    .hero, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 24px;
      box-shadow: 0 18px 42px rgba(15, 23, 42, 0.06);
    }
    .eyebrow {
      display: inline-block;
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    h1, h2, h3 { margin: 0; }
    h1 { margin-top: 16px; font-size: 32px; line-height: 1.15; }
    h2 { font-size: 18px; margin-bottom: 12px; }
    p.meta { margin: 10px 0 0; color: var(--muted); }
    .grid {
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: #fbfdff;
    }
    .metric label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }
    .metric strong {
      display: block;
      margin-top: 8px;
      font-size: 18px;
    }
    .markdown p, .markdown li { line-height: 1.65; }
    .markdown p { margin: 0 0 14px; }
    .markdown strong { font-weight: 700; }
    .markdown code {
      font-family: "SFMono-Regular", "SF Mono", Consolas, monospace;
      font-size: 0.92em;
      background: #edf3ff;
      border: 1px solid #d7e4fb;
      border-radius: 8px;
      padding: 0.08rem 0.35rem;
      color: #1f3b62;
    }
    .markdown pre, .code-block {
      overflow: auto;
      background: #0f172a;
      color: #e2e8f0;
      border-radius: 18px;
      padding: 16px;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .markdown ul { margin: 0 0 16px 20px; }
    .markdown table {
      width: 100%;
      border-collapse: collapse;
      margin: 0 0 16px;
      font-size: 13px;
      background: #fbfdff;
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
    }
    .markdown th, .markdown td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
    }
    .markdown th {
      background: #f3f8ff;
      color: #30445f;
      font-weight: 700;
    }
    .table-wrap { overflow: auto; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #f8fbff;
      font-weight: 700;
      color: #30445f;
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: #f8fbff;
      color: #32465f;
      font-size: 12px;
    }
    .claim {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      background: #fbfdff;
      margin-bottom: 12px;
    }
    .claim-status {
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--success);
    }
    .claim-status.unverified { color: var(--warning); }
    .muted { color: var(--muted); }
    .chart-shell {
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 16px;
      background: #fbfdff;
    }
    .chart-shell img {
      display: block;
      width: 100%;
      height: auto;
      border-radius: 16px;
      border: 1px solid #dbe6f5;
      background: #ffffff;
    }
    .footer {
      color: var(--muted);
      font-size: 12px;
      text-align: center;
      padding-bottom: 16px;
    }
    @media print {
      body { padding: 0; background: #fff; }
      .hero, .panel { box-shadow: none; break-inside: avoid; }
    }
    """

    def build_bundle(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        assistant_message: ChatMessageItem,
        question: str | None,
        execution_detail: ExecutionDetailResponse | None,
    ) -> AnalysisExportBundle:
        """Build a canonical immutable bundle from persisted chat and execution snapshots."""

        analysis_document = self._resolve_analysis_document(assistant_message)
        preview = self._resolve_preview(execution_detail, analysis_document)
        execution_summary = self._build_execution_summary(execution_detail)
        execution = self._resolve_execution_result(
            execution_detail,
            assistant_message=assistant_message,
            analysis_document=analysis_document,
            preview=preview,
        )
        visualization = self._resolve_visualization(
            assistant_message,
            analysis_document=analysis_document,
            execution=execution,
        )

        return AnalysisExportBundle(
            question=(question or "Question unavailable for this snapshot.").strip(),
            answer_markdown=assistant_message.content,
            generated_sql=assistant_message.generated_sql
            or (execution_detail.result.normalized_sql if execution_detail and execution_detail.result else None)
            or (execution_detail.history.normalized_sql if execution_detail else None),
            execution_summary=execution_summary,
            preview=preview,
            analysis_document=analysis_document,
            visualization=visualization,
            claims=assistant_message.claims,
            evidence=assistant_message.evidence,
            claim_evidence_links=assistant_message.claim_evidence_links,
            source_metadata=AnalysisSourceMetadata(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                assistant_message_id=assistant_message.id,
                message_created_at=assistant_message.created_at,
                selected_database_connector=assistant_message.selected_connector,
                metadata_sources=assistant_message.metadata_sources,
                context_tables=assistant_message.context_tables,
            ),
            scenario_provenance=AnalysisScenarioProvenance(),
            export_metadata=AnalysisExportMetadata(generated_at=datetime.now(tz=UTC)),
        )

    def render(
        self,
        bundle: AnalysisExportBundle,
        export_format: AnalysisExportFormat,
    ) -> RenderedAnalysisExport:
        """Render a bundle into a downloadable export format."""

        stem = self._build_file_stem(bundle)
        if export_format == AnalysisExportFormat.HTML:
            return RenderedAnalysisExport(
                filename=f"{stem}.html",
                content_type="text/html; charset=utf-8",
                content=self.render_html(bundle).encode("utf-8"),
            )
        if export_format == AnalysisExportFormat.IPYNB:
            return RenderedAnalysisExport(
                filename=f"{stem}.ipynb",
                content_type="application/x-ipynb+json",
                content=self.render_notebook(bundle).encode("utf-8"),
            )
        return RenderedAnalysisExport(
            filename=f"{stem}.pdf",
            content_type="application/pdf",
            content=self.render_pdf(bundle),
        )

    def render_chat_response_chart_png(
        self,
        *,
        response: ChatAskResponse,
        workspace_id: str,
        question: str,
    ) -> bytes | None:
        """Render the primary chart from an in-memory chat response for chat transports."""
        if response.visualization is None:
            return None
        preview = response.execution.preview if response.execution is not None else QueryPreview()
        bundle = AnalysisExportBundle(
            question=question,
            answer_markdown=response.answer,
            generated_sql=response.generated_sql,
            execution_summary=AnalysisExecutionSummary(
                execution_id=response.execution.execution_id if response.execution else None,
                status=response.execution.status if response.execution else None,
                engine=response.execution.engine if response.execution else None,
                row_count=response.execution.row_count if response.execution else None,
                duration_ms=response.execution.duration_ms if response.execution else None,
                query_id=response.execution.query_id if response.execution else None,
                error_message=response.execution.error_message if response.execution else None,
                warnings=response.execution.warnings if response.execution else [],
            ),
            preview=preview,
            analysis_document=response.analysis_document,
            visualization=response.visualization,
            claims=response.claims,
            evidence=response.evidence,
            claim_evidence_links=response.claim_evidence_links,
            source_metadata=AnalysisSourceMetadata(
                workspace_id=workspace_id,
                conversation_id=response.conversation_id or "mattermost",
                assistant_message_id=response.assistant_message_id or "mattermost",
                message_created_at=datetime.now(tz=UTC),
                selected_database_connector=response.selected_database_connector,
                metadata_sources=response.metadata_sources,
                context_tables=response.context_tables,
            ),
            scenario_provenance=AnalysisScenarioProvenance(),
            export_metadata=AnalysisExportMetadata(generated_at=datetime.now(tz=UTC)),
        )
        return self._render_chart_png(bundle)

    def render_html(self, bundle: AnalysisExportBundle) -> str:
        """Render a standalone HTML analyst report."""

        metrics = [
            ("Connector", bundle.source_metadata.selected_database_connector or "not selected"),
            ("Execution", bundle.execution_summary.status or "not executed"),
            (
                "Rows",
                "-"
                if bundle.execution_summary.row_count is None
                else str(bundle.execution_summary.row_count),
            ),
            (
                "Duration",
                "-"
                if bundle.execution_summary.duration_ms is None
                else f"{bundle.execution_summary.duration_ms} ms",
            ),
        ]
        chart_media = self._render_chart_html(bundle)
        preview_table = self._render_preview_table(bundle.preview)
        evidence_html = self._render_evidence_cards(bundle)
        sql_block = (
            f'<pre class="code-block">{escape(bundle.generated_sql)}</pre>'
            if bundle.generated_sql
            else '<p class="muted">SQL snapshot is not available for this export.</p>'
        )

        return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(self._build_title(bundle))}</title>
    <style>{self._HTML_CSS}</style>
  </head>
  <body>
    <main class="report">
      <section class="hero">
        <span class="eyebrow">Rhetorica Export</span>
        <h1>{escape(self._build_title(bundle))}</h1>
        <p class="meta">
          Immutable snapshot from conversation <strong>{escape(bundle.source_metadata.conversation_id)}</strong>,
          message <strong>{escape(bundle.source_metadata.assistant_message_id)}</strong>.
        </p>
        <div class="grid" style="margin-top:20px">
          {''.join(self._render_metric(label, value) for label, value in metrics)}
        </div>
      </section>

      <section class="panel">
        <h2>Question</h2>
        <div class="markdown">{self._markdown_to_html(bundle.question)}</div>
      </section>

      <section class="panel">
        <h2>Answer</h2>
        <div class="markdown">{self._markdown_to_html(bundle.answer_markdown)}</div>
      </section>

      <section class="panel">
        <h2>Visualization</h2>
        <div class="chart-shell">
          {chart_media if chart_media else '<p class="muted">No visualization snapshot was stored for this answer.</p>'}
        </div>
      </section>

      <section class="panel">
        <h2>SQL Snapshot</h2>
        {sql_block}
      </section>

      <section class="panel">
        <h2>Execution Preview</h2>
        {preview_table}
      </section>

      <section class="panel">
        <h2>Evidence</h2>
        {evidence_html}
      </section>

      <section class="panel">
        <h2>Provenance</h2>
        <div class="chip-row">
          <span class="chip">Workspace: {escape(bundle.source_metadata.workspace_id)}</span>
          <span class="chip">Connector: {escape(bundle.source_metadata.selected_database_connector or '-')}</span>
          <span class="chip">Generated: {escape(bundle.export_metadata.generated_at.isoformat())}</span>
        </div>
        <div class="chip-row">
          {''.join(f'<span class="chip">{escape(source)}</span>' for source in bundle.source_metadata.metadata_sources)}
        </div>
      </section>

      <p class="footer">Exported from an immutable analysis snapshot. No SQL or LLM step was re-run.</p>
    </main>
  </body>
</html>"""

    def render_notebook(self, bundle: AnalysisExportBundle) -> str:
        """Render a deterministic notebook handoff."""

        preview_rows = [
            dict(zip(bundle.preview.columns, row, strict=False))
            for row in bundle.preview.rows[: self._MAX_TABLE_ROWS]
        ]
        chart_markdown = self._render_chart_notebook_markdown(bundle)
        notebook = {
            "cells": [
                self._markdown_cell(
                    f"# {self._build_title(bundle)}\n\n"
                    "Immutable export bundle for analyst handoff. No live query is executed by default."
                ),
                self._markdown_cell(
                    f"## Question\n\n{bundle.question}\n\n## Answer\n\n{bundle.answer_markdown}"
                ),
                self._markdown_cell(chart_markdown),
                self._code_cell(bundle.generated_sql or "-- SQL snapshot unavailable"),
                self._code_cell(
                    "# Optional: load a fresh dataframe only if governance policy allows it.\n"
                    "# Do not paste secrets here.\n"
                    "df = None"
                ),
                self._code_cell(
                    "preview_rows = "
                    + json.dumps(preview_rows, ensure_ascii=False, indent=2)
                    + "\npreview_rows"
                ),
                self._markdown_cell(
                    "## Evidence Appendix\n\n"
                    + self._render_notebook_evidence_markdown(bundle)
                ),
                self._code_cell(
                    "chart_spec = "
                    + json.dumps(
                        bundle.visualization.model_dump(mode="json") if bundle.visualization else None,
                        ensure_ascii=False,
                        indent=2,
                    )
                ),
            ],
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {"name": "python", "version": "3.12"},
                "rhetorica_export": {
                    "bundle_version": bundle.export_metadata.bundle_version,
                    "source_kind": bundle.export_metadata.source_kind,
                    "workspace_id": bundle.source_metadata.workspace_id,
                },
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        return json.dumps(notebook, ensure_ascii=False, indent=2)

    def render_pdf(self, bundle: AnalysisExportBundle) -> bytes:
        """Render a compact PDF summary from the immutable bundle."""

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Image as PlatypusImage
        from reportlab.platypus import (
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
        )

        buffer = BytesIO()
        font_name = self._resolve_pdf_font(TTFont, pdfmetrics)
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=48,
            rightMargin=48,
            topMargin=44,
            bottomMargin=44,
            title=self._build_title(bundle),
            author="Rhetorica",
            subject="Immutable analysis export",
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "RhetoricaPdfTitle",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#182433"),
            spaceAfter=8,
        )
        heading_style = ParagraphStyle(
            "RhetoricaPdfHeading",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=13,
            leading=16,
            textColor=colors.HexColor("#182433"),
            spaceBefore=8,
            spaceAfter=8,
        )
        body_style = ParagraphStyle(
            "RhetoricaPdfBody",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#223247"),
            spaceAfter=6,
        )
        meta_style = ParagraphStyle(
            "RhetoricaPdfMeta",
            parent=body_style,
            textColor=colors.HexColor("#5d6b82"),
            spaceAfter=12,
        )
        code_style = ParagraphStyle(
            "RhetoricaPdfCode",
            parent=body_style,
            fontName=font_name,
            fontSize=9,
            leading=12,
            backColor=colors.HexColor("#f3f8ff"),
            borderColor=colors.HexColor("#d8e1ef"),
            borderWidth=1,
            borderPadding=8,
            borderRadius=6,
            spaceAfter=8,
        )
        story: list[object] = [
            Paragraph(escape(self._build_title(bundle)), title_style),
            Paragraph(
                escape("Rhetorica immutable analysis export. No SQL or LLM step was re-run."),
                meta_style,
            ),
        ]

        chart_png = self._render_chart_png(bundle)
        if chart_png is not None:
            story.append(Paragraph("Visualization", heading_style))
            story.append(PlatypusImage(BytesIO(chart_png), width=516, height=237))
            story.append(Spacer(1, 10))

        self._append_pdf_markdown_section(
            story,
            "Question",
            bundle.question,
            heading_style,
            body_style,
        )
        self._append_pdf_markdown_section(
            story,
            "Answer",
            bundle.answer_markdown,
            heading_style,
            body_style,
        )

        story.append(Paragraph("Execution Summary", heading_style))
        for line in [
            f"Connector: {bundle.source_metadata.selected_database_connector or 'not selected'}",
            f"Execution status: {bundle.execution_summary.status or 'not executed'}",
            f"Rows: {bundle.execution_summary.row_count if bundle.execution_summary.row_count is not None else '-'}",
            f"Duration: {bundle.execution_summary.duration_ms if bundle.execution_summary.duration_ms is not None else '-'} ms",
        ]:
            story.append(Paragraph(escape(line), body_style))
        story.append(Spacer(1, 6))

        story.append(Paragraph("SQL Snapshot", heading_style))
        story.append(
            Preformatted(
                bundle.generated_sql or "SQL snapshot unavailable.",
                style=code_style,
                maxLineLength=88,
            )
        )

        story.append(Paragraph("Preview", heading_style))
        preview_table = self._build_pdf_preview_table(bundle.preview, font_name)
        if preview_table is None:
            story.append(Paragraph("Preview sample is unavailable for this snapshot.", body_style))
        else:
            story.append(preview_table)
            story.append(Spacer(1, 8))

        self._append_pdf_markdown_section(
            story,
            "Evidence",
            self._render_evidence_as_text(bundle),
            heading_style,
            body_style,
        )

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def _build_execution_summary(
        execution_detail: ExecutionDetailResponse | None,
    ) -> AnalysisExecutionSummary:
        if execution_detail is None:
            return AnalysisExecutionSummary()

        response = execution_detail.result
        history = execution_detail.history
        return AnalysisExecutionSummary(
            execution_id=history.execution_id,
            status=(response.status if response is not None else history.status),
            engine=(response.engine if response is not None else history.engine),
            row_count=(response.row_count if response is not None else history.row_count),
            duration_ms=(response.duration_ms if response is not None else history.duration_ms),
            query_id=(response.query_id if response is not None else history.query_id),
            error_message=(
                response.error_message if response is not None else history.error_message
            ),
            warnings=response.warnings if response is not None else history.warnings,
        )

    @staticmethod
    def _resolve_preview(
        execution_detail: ExecutionDetailResponse | None,
        analysis_document: AnalysisDocument | None,
    ) -> QueryPreview:
        if execution_detail is not None:
            if execution_detail.result is not None:
                return execution_detail.result.preview
            return execution_detail.history.preview

        if analysis_document is not None:
            for block in analysis_document.blocks:
                preview = getattr(block, "preview", None)
                if isinstance(preview, QueryPreview) and preview.columns:
                    return preview

        return QueryPreview()

    @staticmethod
    def _resolve_analysis_document(
        assistant_message: ChatMessageItem,
    ) -> AnalysisDocument | None:
        if not assistant_message.analysis_document:
            return None
        return AnalysisDocument.model_validate(assistant_message.analysis_document)

    @classmethod
    def _resolve_execution_result(
        cls,
        execution_detail: ExecutionDetailResponse | None,
        *,
        assistant_message: ChatMessageItem,
        analysis_document: AnalysisDocument | None,
        preview: QueryPreview,
    ) -> ExecuteSqlResponse | None:
        if execution_detail is not None:
            if execution_detail.result is not None:
                return execution_detail.result
            return ExecuteSqlResponse(
                execution_id=execution_detail.history.execution_id,
                status=execution_detail.history.status,
                engine=execution_detail.history.engine,
                query_id=execution_detail.history.query_id,
                normalized_sql=execution_detail.history.normalized_sql,
                row_count=execution_detail.history.row_count,
                duration_ms=execution_detail.history.duration_ms,
                warnings=execution_detail.history.warnings,
                violations=execution_detail.history.violations,
                preview=execution_detail.history.preview,
                error_message=execution_detail.history.error_message,
            )

        if not preview.columns:
            return None

        source_execution_id = assistant_message.execution_id
        if source_execution_id is None and analysis_document is not None:
            chart_block = cls._primary_chart_block(analysis_document)
            if chart_block is not None:
                source_execution_id = chart_block.source_execution_id

        if source_execution_id is None:
            return None

        return ExecuteSqlResponse(
            execution_id=source_execution_id,
            status="succeeded",
            engine="clickhouse",
            preview=preview,
            row_count=len(preview.rows),
            normalized_sql=assistant_message.generated_sql,
        )

    @classmethod
    def _resolve_visualization(
        cls,
        assistant_message: ChatMessageItem,
        *,
        analysis_document: AnalysisDocument | None,
        execution: ExecuteSqlResponse | None,
    ) -> VisualizationSpec | None:
        primary = primary_visualization_from_document(analysis_document)
        candidate: dict[str, Any] | None = None
        if primary is not None:
            candidate = primary.model_dump(mode="json")
        elif assistant_message.visualization_spec:
            candidate = assistant_message.visualization_spec

        if candidate is None:
            return None
        if execution is None:
            return VisualizationSpec.model_validate(candidate)

        return VisualizationPlanner().plan(execution=execution, llm_candidate=candidate)

    @staticmethod
    def _primary_chart_block(
        analysis_document: AnalysisDocument | None,
    ) -> AnalysisChartBlock | None:
        if analysis_document is None:
            return None
        for block in analysis_document.blocks:
            if isinstance(block, AnalysisChartBlock):
                return block
        return None

    @staticmethod
    def _build_file_stem(bundle: AnalysisExportBundle) -> str:
        base = AnalysisExportService._slugify(bundle.question) or "analysis-export"
        return f"{base[:48]}-{bundle.source_metadata.assistant_message_id[:8]}"

    @staticmethod
    def _build_title(bundle: AnalysisExportBundle) -> str:
        if bundle.analysis_document and bundle.analysis_document.title:
            return bundle.analysis_document.title
        if bundle.visualization and bundle.visualization.title:
            return bundle.visualization.title
        question = bundle.question.strip()
        return question[:96] if question else "Rhetorica analysis export"

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"\s+", "-", value.strip().lower())
        normalized = re.sub(r"[^a-z0-9\u0400-\u04FF_-]+", "-", normalized)
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized

    @staticmethod
    def _render_metric(label: str, value: str) -> str:
        return (
            '<div class="metric">'
            f"<label>{escape(label)}</label>"
            f"<strong>{escape(value)}</strong>"
            "</div>"
        )

    def _render_preview_table(self, preview: QueryPreview) -> str:
        if not preview.columns:
            return '<p class="muted">Preview sample is unavailable for this snapshot.</p>'
        head = "".join(f"<th>{escape(str(column))}</th>" for column in preview.columns)
        rows = []
        for row in preview.rows[: self._MAX_TABLE_ROWS]:
            cells = "".join(f"<td>{escape(self._stringify_cell(cell))}</td>" for cell in row)
            rows.append(f"<tr>{cells}</tr>")
        return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

    def _render_evidence_cards(self, bundle: AnalysisExportBundle) -> str:
        if not bundle.claims:
            return '<p class="muted">No evidence bundle was attached to this answer.</p>'
        evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
        sections = []
        for claim in bundle.claims:
            links = next(
                (
                    item.evidence_ids
                    for item in bundle.claim_evidence_links
                    if item.claim_id == claim.claim_id
                ),
                [],
            )
            linked = [evidence_by_id[item] for item in links if item in evidence_by_id]
            linked_html = "".join(
                (
                    '<div class="chip">'
                    f"{escape(self._enum_value(item.type))}: {escape(item.reference)}"
                    f"{' • ' + escape(item.title) if item.title else ''}"
                    "</div>"
                )
                for item in linked
            )
            sections.append(
                '<div class="claim">'
                f'<div class="claim-status {"unverified" if claim.status.value == "unverified" else ""}">{escape(claim.status.value)}</div>'
                f"<p>{escape(claim.text)}</p>"
                f'<div class="chip-row">{linked_html or "<span class=\"muted\">No linked evidence references.</span>"}</div>'
                "</div>"
            )
        return "".join(sections)

    def _render_evidence_as_text(self, bundle: AnalysisExportBundle) -> str:
        if not bundle.claims:
            return "No evidence bundle was attached to this answer."
        evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
        blocks: list[str] = []
        for claim in bundle.claims:
            blocks.append(f"[{claim.status.value}] {claim.text}")
            links = next(
                (
                    item.evidence_ids
                    for item in bundle.claim_evidence_links
                    if item.claim_id == claim.claim_id
                ),
                [],
            )
            for evidence_id in links:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                blocks.append(
                    f"  - {self._enum_value(evidence.type)}: {evidence.reference}"
                    + (f" ({evidence.title})" if evidence.title else "")
                )
        return "\n".join(blocks)

    def _render_preview_as_text(self, preview: QueryPreview) -> str:
        if not preview.columns:
            return "Preview sample is unavailable for this snapshot."
        rows = [" | ".join(preview.columns)]
        for row in preview.rows[: self._MAX_TABLE_ROWS]:
            rows.append(" | ".join(self._stringify_cell(cell) for cell in row))
        return "\n".join(rows)

    def _append_pdf_markdown_section(
        self,
        story: list[object],
        heading: str,
        markdown: str,
        heading_style: Any,
        body_style: Any,
    ) -> None:
        from reportlab.platypus import Paragraph, Spacer

        story.append(Paragraph(heading, heading_style))
        blocks = self._markdown_to_pdf_blocks(markdown)
        if not blocks:
            story.append(Paragraph("-", body_style))
            story.append(Spacer(1, 6))
            return
        for block in blocks:
            if block["kind"] == "paragraph":
                story.append(Paragraph(escape(block["text"]).replace("\n", "<br/>"), body_style))
            elif block["kind"] == "list":
                for item in block["items"]:
                    story.append(Paragraph(escape(f"• {item}"), body_style))
            elif block["kind"] == "table":
                table_lines = [" | ".join(block["headers"])]
                for row in block["rows"]:
                    table_lines.append(" | ".join(row))
                story.append(Paragraph(escape("\n".join(table_lines)).replace("\n", "<br/>"), body_style))
        story.append(Spacer(1, 6))

    def _build_pdf_preview_table(self, preview: QueryPreview, font_name: str) -> object | None:
        if not preview.columns:
            return None

        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle

        data = [preview.columns]
        for row in preview.rows[: self._MAX_TABLE_ROWS]:
            data.append([self._stringify_cell(cell) for cell in row])

        column_count = max(len(preview.columns), 1)
        widths = [516 / column_count] * column_count
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LEADING", (0, 0), (-1, -1), 11),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f8ff")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#30445f")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e1ef")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    @staticmethod
    def _markdown_cell(source: str) -> dict[str, Any]:
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": [source],
        }

    @staticmethod
    def _code_cell(source: str) -> dict[str, Any]:
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [source],
        }

    def _render_notebook_evidence_markdown(self, bundle: AnalysisExportBundle) -> str:
        if not bundle.claims:
            return "No evidence bundle was attached to this answer."
        parts: list[str] = []
        evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
        for claim in bundle.claims:
            parts.append(f"- **{claim.status.value}** {claim.text}")
            links = next(
                (
                    item.evidence_ids
                    for item in bundle.claim_evidence_links
                    if item.claim_id == claim.claim_id
                ),
                [],
            )
            for evidence_id in links:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                parts.append(
                    f"  - `{self._enum_value(evidence.type)}` {evidence.reference}"
                    + (f" - {evidence.title}" if evidence.title else "")
                )
        return "\n".join(parts)

    @staticmethod
    def _stringify_cell(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, float):
            return f"{value:.4f}".rstrip("0").rstrip(".")
        return str(value)

    @staticmethod
    def _enum_value(value: object) -> str:
        if hasattr(value, "value"):
            return str(value.value)
        return str(value)

    def _render_chart_html(self, bundle: AnalysisExportBundle) -> str | None:
        chart_png = self._render_chart_png(bundle)
        if chart_png is None:
            return None
        encoded = base64.b64encode(chart_png).decode("ascii")
        title = bundle.visualization.title if bundle.visualization else self._build_title(bundle)
        return (
            f'<img alt="{escape(title)}" '
            f'src="data:image/png;base64,{encoded}" />'
        )

    def _render_chart_notebook_markdown(self, bundle: AnalysisExportBundle) -> str:
        chart_html = self._render_chart_html(bundle)
        if chart_html is None:
            return "## Visualization\n\nNo visualization snapshot was stored for this answer."
        return f"## Visualization\n\n{chart_html}"

    def _render_chart_png(self, bundle: AnalysisExportBundle) -> bytes | None:
        snapshot = self._build_cartesian_chart_snapshot(bundle)
        if snapshot is None:
            return None

        width = 1400
        height = 760
        image = Image.new("RGBA", (width, height), "#fbfdff")
        draw = ImageDraw.Draw(image)
        title_font = self._image_font(30)
        body_font = self._image_font(17)
        small_font = self._image_font(14)
        left = 100
        right = width - 72
        header_y = 32
        title_lines = self._wrap_text_to_width(
            draw=draw,
            text=snapshot.title,
            font=title_font,
            max_width=right - left,
            max_lines=2,
        )
        title_line_height = self._text_height(draw=draw, text="Ag", font=title_font)
        header_bottom = header_y + len(title_lines) * title_line_height + max(
            0,
            len(title_lines) - 1,
        ) * 6
        if snapshot.summary:
            summary_lines = self._wrap_text_to_width(
                draw=draw,
                text=snapshot.summary,
                font=body_font,
                max_width=right - left,
                max_lines=2,
            )
            summary_line_height = self._text_height(draw=draw, text="Ag", font=body_font)
            header_bottom += (
                12
                + len(summary_lines) * summary_line_height
                + max(0, len(summary_lines) - 1) * 5
            )
        top = max(128, int(header_bottom + 26))
        bottom = height - 150
        plot_width = right - left
        plot_height = bottom - top

        draw.rounded_rectangle(
            [(0, 0), (width - 1, height - 1)],
            radius=28,
            fill="#fbfdff",
            outline="#dbe6f5",
            width=2,
        )
        header_bottom = self._draw_wrapped_text(
            draw=draw,
            xy=(left, header_y),
            text=snapshot.title,
            font=title_font,
            fill="#182433",
            max_width=right - left,
            max_lines=2,
            line_gap=6,
        )
        if snapshot.summary:
            self._draw_wrapped_text(
                draw=draw,
                xy=(left, header_bottom + 12),
                text=snapshot.summary,
                font=body_font,
                fill="#5d6b82",
                max_width=right - left,
                max_lines=2,
                line_gap=5,
            )

        draw.rounded_rectangle(
            [(left - 18, top - 18), (right + 18, bottom + 18)],
            radius=24,
            fill="#ffffff",
            outline="#dbe6f5",
            width=2,
        )

        max_value = max(
            (
                self._stacked_max(snapshot.series, len(snapshot.labels))
                if snapshot.stacked
                else max((value for item in snapshot.series for value in item.values if value is not None), default=0.0)
            ),
            0.0,
        )
        if max_value <= 0:
            return None

        for tick in range(6):
            ratio = tick / 5
            y = bottom - ratio * plot_height
            draw.line([(left, y), (right, y)], fill="#e5edf8", width=2)
            label = self._format_numeric_tick(max_value * ratio)
            bbox = draw.textbbox((0, 0), label, font=small_font)
            draw.text((left - 16 - (bbox[2] - bbox[0]), y - 8), label, fill="#6b7b93", font=small_font)

        draw.line([(left, top), (left, bottom)], fill="#bfd0e8", width=3)
        draw.line([(left, bottom), (right, bottom)], fill="#bfd0e8", width=3)

        if snapshot.mode == "bar":
            self._draw_bar_chart(
                draw=draw,
                snapshot=snapshot,
                left=left,
                bottom=bottom,
                plot_width=plot_width,
                plot_height=plot_height,
            )
        else:
            self._draw_line_chart(
                draw=draw,
                snapshot=snapshot,
                left=left,
                bottom=bottom,
                plot_width=plot_width,
                plot_height=plot_height,
            )

        step = plot_width / max(len(snapshot.labels), 1)
        for index, label in enumerate(snapshot.labels):
            x = left + (index + 0.5) * step
            clipped = label[:14] + "…" if len(label) > 15 else label
            bbox = draw.textbbox((0, 0), clipped, font=small_font)
            draw.text((x - (bbox[2] - bbox[0]) / 2, bottom + 18), clipped, fill="#6b7b93", font=small_font)

        legend_y = height - 84
        cursor_x = left
        for series in snapshot.series:
            if cursor_x > right - 140:
                break
            draw.rounded_rectangle(
                [(cursor_x, legend_y), (cursor_x + 22, legend_y + 22)],
                radius=7,
                fill=series.color,
                outline=series.color,
            )
            series_label = self._truncate_text_to_width(
                draw=draw,
                text=series.name,
                font=body_font,
                max_width=210,
            )
            draw.text(
                (cursor_x + 32, legend_y - 1),
                series_label,
                fill="#30445f",
                font=body_font,
            )
            cursor_x += 260

        footer = "Snapshot built from stored execution preview."
        if snapshot.x_axis_label or snapshot.y_axis_label:
            footer = (
                f"X: {snapshot.x_axis_label or '-'}  |  "
                f"Y: {snapshot.y_axis_label or '-'}"
            )
        draw.text((left, height - 42), footer, fill="#7c8aa0", font=small_font)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _build_cartesian_chart_snapshot(
        self,
        bundle: AnalysisExportBundle,
    ) -> _CartesianChartSnapshot | None:
        spec = bundle.visualization
        preview = self._chart_preview(bundle)
        if spec is None or not preview.columns or not preview.rows:
            return None

        series = self._build_chart_series(spec, preview)
        if not series:
            return None

        mode = "line"
        if spec.kind in {
            VisualizationKind.BAR,
            VisualizationKind.BAR_GROUPED,
            VisualizationKind.BAR_STACKED,
            VisualizationKind.DONUT,
            VisualizationKind.PIE,
            VisualizationKind.FUNNEL,
            VisualizationKind.HEATMAP,
            VisualizationKind.TREEMAP,
            VisualizationKind.WATERFALL,
            VisualizationKind.HISTOGRAM,
        }:
            mode = "bar"

        stacked = spec.kind in {
            VisualizationKind.AREA_STACKED,
            VisualizationKind.BAR_STACKED,
        } or bool(spec.encoding.stack)

        return _CartesianChartSnapshot(
            title=spec.title or self._build_title(bundle),
            summary=spec.summary or spec.description or spec.rationale.why_kind,
            x_axis_label=spec.x_axis_label,
            y_axis_label=spec.y_axis_label,
            labels=self._build_chart_labels(spec, preview),
            series=series,
            mode=mode,
            stacked=stacked,
        )

    def _chart_preview(self, bundle: AnalysisExportBundle) -> QueryPreview:
        chart_block = self._primary_chart_block(bundle.analysis_document)
        if chart_block is not None and chart_block.preview is not None and chart_block.preview.columns:
            return chart_block.preview
        return bundle.preview

    def _build_chart_labels(
        self,
        spec: VisualizationSpec,
        preview: QueryPreview,
    ) -> list[str]:
        x_index = self._find_column_index(preview, spec.encoding.x)
        if x_index is None:
            return []
        labels: list[str] = []
        seen: set[str] = set()
        for row in preview.rows:
            label = self._stringify_cell(row[x_index])
            if label in seen:
                continue
            seen.add(label)
            labels.append(label)
        return labels

    def _build_chart_series(
        self,
        spec: VisualizationSpec,
        preview: QueryPreview,
    ) -> list[_ChartSeries]:
        labels = self._build_chart_labels(spec, preview)
        if not labels:
            return []

        colors = ["#2563eb", "#0f766e", "#d97706", "#b91c1c", "#7c3aed", "#0ea5e9"]
        x_index = self._find_column_index(preview, spec.encoding.x)
        if x_index is None:
            return []

        series_index = self._find_column_index(preview, spec.encoding.series)
        if series_index is not None and spec.encoding.y:
            metric_index = self._find_column_index(preview, spec.encoding.y[0])
            if metric_index is None:
                return []
            values_by_series: dict[str, dict[str, float]] = {}
            series_order: list[str] = []
            for row in preview.rows:
                label = self._stringify_cell(row[x_index])
                series_name = self._stringify_cell(row[series_index])
                value = self._to_float(row[metric_index])
                if value is None:
                    continue
                if series_name not in values_by_series:
                    values_by_series[series_name] = {}
                    series_order.append(series_name)
                values_by_series[series_name][label] = values_by_series[series_name].get(label, 0.0) + value
            return [
                _ChartSeries(
                    name=name,
                    values=[values_by_series[name].get(label) for label in labels],
                    color=colors[index % len(colors)],
                )
                for index, name in enumerate(series_order)
            ]

        series: list[_ChartSeries] = []
        for index, name in enumerate(spec.encoding.y):
            column_index = self._find_column_index(preview, name)
            if column_index is None:
                continue
            values_by_label: dict[str, float] = {}
            for row in preview.rows:
                label = self._stringify_cell(row[x_index])
                value = self._to_float(row[column_index])
                if value is None:
                    continue
                values_by_label[label] = values_by_label.get(label, 0.0) + value
            series.append(
                _ChartSeries(
                    name=self._humanize_label(name),
                    values=[values_by_label.get(label) for label in labels],
                    color=colors[index % len(colors)],
                )
            )
        return series

    def _draw_line_chart(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        snapshot: _CartesianChartSnapshot,
        left: int,
        bottom: int,
        plot_width: int,
        plot_height: int,
    ) -> None:
        max_value = self._stacked_max(snapshot.series, len(snapshot.labels)) if snapshot.stacked else max(
            (value for item in snapshot.series for value in item.values if value is not None),
            default=0.0,
        )
        if max_value <= 0:
            return

        step = plot_width / max(len(snapshot.labels), 1)
        running = [0.0] * len(snapshot.labels)
        for series in snapshot.series:
            points: list[tuple[float, float]] = []
            base_points: list[tuple[float, float]] = []
            for index, value in enumerate(series.values):
                current = value or 0.0
                base = running[index] if snapshot.stacked else 0.0
                top_value = base + current if snapshot.stacked else current
                x = left + (index + 0.5) * step
                y = bottom - (top_value / max_value) * plot_height
                points.append((x, y))
                if snapshot.stacked:
                    base_y = bottom - (base / max_value) * plot_height
                    base_points.append((x, base_y))
                    running[index] = top_value

            if snapshot.stacked:
                polygon = points + list(reversed(base_points))
                draw.polygon(polygon, fill=self._hex_to_rgba(series.color, 70), outline=series.color)
            if len(points) >= 2:
                draw.line(points, fill=series.color, width=6)
            for x, y in points:
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=series.color, outline="#ffffff", width=2)

    def _draw_bar_chart(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        snapshot: _CartesianChartSnapshot,
        left: int,
        bottom: int,
        plot_width: int,
        plot_height: int,
    ) -> None:
        max_value = self._stacked_max(snapshot.series, len(snapshot.labels)) if snapshot.stacked else max(
            (value for item in snapshot.series for value in item.values if value is not None),
            default=0.0,
        )
        if max_value <= 0:
            return

        step = plot_width / max(len(snapshot.labels), 1)
        if snapshot.stacked:
            for label_index in range(len(snapshot.labels)):
                x0 = left + label_index * step + step * 0.2
                x1 = left + (label_index + 1) * step - step * 0.2
                stack_bottom = bottom
                for series in snapshot.series:
                    value = series.values[label_index] or 0.0
                    height = (value / max_value) * plot_height
                    y0 = stack_bottom - height
                    draw.rounded_rectangle((x0, y0, x1, stack_bottom), radius=9, fill=series.color)
                    stack_bottom = y0
            return

        group_width = step * 0.72
        bar_width = group_width / max(len(snapshot.series), 1)
        for label_index in range(len(snapshot.labels)):
            group_left = left + label_index * step + (step - group_width) / 2
            for series_index, series in enumerate(snapshot.series):
                value = series.values[label_index]
                if value is None:
                    continue
                height = (value / max_value) * plot_height
                x0 = group_left + series_index * bar_width
                x1 = x0 + bar_width - 6
                y0 = bottom - height
                draw.rounded_rectangle((x0, y0, x1, bottom), radius=8, fill=series.color)

    @staticmethod
    def _find_column_index(preview: QueryPreview, name: str | None) -> int | None:
        if name is None:
            return None
        try:
            return preview.columns.index(name)
        except ValueError:
            return None

    @staticmethod
    def _to_float(value: object) -> float | None:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @classmethod
    def _image_font(cls, size: int) -> ImageFont.ImageFont:
        font_path = cls._font_path()
        if font_path is None:
            return ImageFont.load_default()
        return ImageFont.truetype(font_path, size=size)

    @classmethod
    def _draw_wrapped_text(
        cls,
        *,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: str,
        max_width: int,
        max_lines: int,
        line_gap: int,
    ) -> int:
        x, y = xy
        lines = cls._wrap_text_to_width(
            draw=draw,
            text=text,
            font=font,
            max_width=max_width,
            max_lines=max_lines,
        )
        cursor_y = y
        line_height = cls._text_height(draw=draw, text="Ag", font=font)
        for line in lines:
            draw.text((x, cursor_y), line, fill=fill, font=font)
            cursor_y += line_height + line_gap
        return cursor_y - line_gap if lines else y

    @classmethod
    def _wrap_text_to_width(
        cls,
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        words = [word for word in text.replace("\n", " ").split(" ") if word]
        if not words or max_lines <= 0:
            return []

        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if cls._text_width(draw=draw, text=candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = word
            else:
                lines.append(
                    cls._truncate_text_to_width(
                        draw=draw,
                        text=word,
                        font=font,
                        max_width=max_width,
                    )
                )
                current = ""
            if len(lines) == max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if words and len(lines) == max_lines:
            consumed = " ".join(lines).removesuffix("...").strip()
            original = " ".join(words)
            if len(consumed) < len(original):
                lines[-1] = cls._truncate_text_to_width(
                    draw=draw,
                    text=lines[-1],
                    font=font,
                    max_width=max_width,
                )
        return lines

    @classmethod
    def _truncate_text_to_width(
        cls,
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> str:
        if cls._text_width(draw=draw, text=text, font=font) <= max_width:
            return text
        suffix = "..."
        available = max(0, max_width - cls._text_width(draw=draw, text=suffix, font=font))
        clipped = text
        while clipped and cls._text_width(draw=draw, text=clipped, font=font) > available:
            clipped = clipped[:-1]
        return f"{clipped.rstrip()}{suffix}" if clipped else suffix

    @staticmethod
    def _text_width(
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
    ) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    @staticmethod
    def _text_height(
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
    ) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    @staticmethod
    def _stacked_max(series: list[_ChartSeries], label_count: int) -> float:
        if not series or label_count <= 0:
            return 0.0
        return max(
            sum(item.values[index] or 0.0 for item in series)
            for index in range(label_count)
        )

    @staticmethod
    def _hex_to_rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
        normalized = value.lstrip("#")
        if len(normalized) != 6:
            return (37, 99, 235, alpha)
        red = int(normalized[0:2], 16)
        green = int(normalized[2:4], 16)
        blue = int(normalized[4:6], 16)
        return (red, green, blue, alpha)

    @staticmethod
    def _format_numeric_tick(value: float) -> str:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        if value.is_integer():
            return str(int(value))
        return f"{value:.1f}"

    @staticmethod
    def _humanize_label(value: str) -> str:
        return value.replace("_", " ").strip().title()

    @staticmethod
    @lru_cache(maxsize=1)
    def _font_path() -> str | None:
        for candidate in (
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/DejaVu Sans.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/local/share/fonts/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        ):
            if Path(candidate).exists():
                return candidate
        return None

    @classmethod
    def _resolve_pdf_font(cls, ttfont_cls: Any, pdfmetrics: Any) -> str:
        font_path = cls._font_path()
        if font_path is None:
            return "Helvetica"
        font_name = "RhetoricaExportFont"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(ttfont_cls(font_name, font_path))
        return font_name

    def _markdown_to_html(self, markdown: str) -> str:
        lines = markdown.splitlines()
        parts: list[str] = []
        paragraph: list[str] = []
        bullet_items: list[str] = []
        in_code = False
        code_lines: list[str] = []

        def flush_paragraph() -> None:
            if paragraph:
                rendered = self._render_inline_markdown(" ".join(paragraph).strip())
                parts.append(f"<p>{rendered}</p>")
                paragraph.clear()

        def flush_bullets() -> None:
            if bullet_items:
                items = "".join(
                    f"<li>{self._render_inline_markdown(item)}</li>" for item in bullet_items
                )
                parts.append(f"<ul>{items}</ul>")
                bullet_items.clear()

        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if stripped.startswith("```"):
                flush_paragraph()
                flush_bullets()
                if in_code:
                    parts.append(f"<pre>{escape(chr(10).join(code_lines))}</pre>")
                    code_lines.clear()
                in_code = not in_code
                index += 1
                continue
            if in_code:
                code_lines.append(line)
                index += 1
                continue
            if not stripped:
                flush_paragraph()
                flush_bullets()
                index += 1
                continue
            if self._looks_like_table_header(lines, index):
                flush_paragraph()
                flush_bullets()
                table_html, next_index = self._render_markdown_table(lines, index)
                parts.append(table_html)
                index = next_index
                continue
            if stripped.startswith("- "):
                flush_paragraph()
                bullet_items.append(stripped[2:].strip())
                index += 1
                continue
            if stripped.startswith("# "):
                flush_paragraph()
                flush_bullets()
                parts.append(f"<h3>{self._render_inline_markdown(stripped[2:].strip())}</h3>")
                index += 1
                continue
            paragraph.append(stripped)
            index += 1

        flush_paragraph()
        flush_bullets()
        if code_lines:
            parts.append(f"<pre>{escape(chr(10).join(code_lines))}</pre>")
        return "".join(parts) or "<p></p>"

    def _markdown_to_pdf_blocks(self, markdown: str) -> list[dict[str, object]]:
        lines = markdown.splitlines()
        blocks: list[dict[str, object]] = []
        paragraph: list[str] = []
        bullet_items: list[str] = []
        in_code = False
        code_lines: list[str] = []

        def flush_paragraph() -> None:
            if paragraph:
                blocks.append({"kind": "paragraph", "text": self._markdown_to_plain_text(" ".join(paragraph))})
                paragraph.clear()

        def flush_bullets() -> None:
            if bullet_items:
                blocks.append(
                    {
                        "kind": "list",
                        "items": [self._markdown_to_plain_text(item) for item in bullet_items],
                    }
                )
                bullet_items.clear()

        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if stripped.startswith("```"):
                flush_paragraph()
                flush_bullets()
                if in_code and code_lines:
                    blocks.append({"kind": "paragraph", "text": "\n".join(code_lines)})
                    code_lines.clear()
                in_code = not in_code
                index += 1
                continue
            if in_code:
                code_lines.append(line)
                index += 1
                continue
            if not stripped:
                flush_paragraph()
                flush_bullets()
                index += 1
                continue
            if self._looks_like_table_header(lines, index):
                flush_paragraph()
                flush_bullets()
                headers = self._split_markdown_row(lines[index])
                index += 2
                rows: list[list[str]] = []
                while index < len(lines):
                    candidate = lines[index].strip()
                    if not candidate or "|" not in candidate:
                        break
                    rows.append(self._split_markdown_row(lines[index]))
                    index += 1
                blocks.append({"kind": "table", "headers": headers, "rows": rows})
                continue
            if stripped.startswith("- "):
                flush_paragraph()
                bullet_items.append(stripped[2:].strip())
                index += 1
                continue
            if stripped.startswith("#"):
                flush_paragraph()
                flush_bullets()
                blocks.append(
                    {
                        "kind": "paragraph",
                        "text": self._markdown_to_plain_text(stripped.lstrip("#").strip()),
                    }
                )
                index += 1
                continue
            paragraph.append(stripped)
            index += 1

        flush_paragraph()
        flush_bullets()
        if code_lines:
            blocks.append({"kind": "paragraph", "text": "\n".join(code_lines)})
        return blocks

    def _markdown_to_plain_text(self, text: str) -> str:
        plain = re.sub(r"`([^`]+)`", r"\1", text)
        plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
        plain = re.sub(r"\*(.+?)\*", r"\1", plain)
        return plain.strip()

    @staticmethod
    def _looks_like_table_header(lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header:
            return False
        return bool(re.match(r"^\|?[\s:-]+(?:\|[\s:-]+)+\|?$", separator))

    def _render_markdown_table(self, lines: list[str], start: int) -> tuple[str, int]:
        rows: list[list[str]] = [self._split_markdown_row(lines[start])]
        index = start + 2
        while index < len(lines):
            stripped = lines[index].strip()
            if "|" not in stripped or not stripped:
                break
            rows.append(self._split_markdown_row(lines[index]))
            index += 1

        header = rows[0]
        body = rows[1:]
        head_html = "".join(f"<th>{self._render_inline_markdown(cell)}</th>" for cell in header)
        body_html = "".join(
            "<tr>"
            + "".join(f"<td>{self._render_inline_markdown(cell)}</td>" for cell in row)
            + "</tr>"
            for row in body
        )
        table_html = (
            "<table><thead><tr>"
            f"{head_html}"
            "</tr></thead><tbody>"
            f"{body_html}"
            "</tbody></table>"
        )
        return table_html, index

    @staticmethod
    def _split_markdown_row(line: str) -> list[str]:
        normalized = line.strip().strip("|")
        return [cell.strip() for cell in normalized.split("|")]

    def _render_inline_markdown(self, text: str) -> str:
        segments = re.split(r"(`[^`]+`)", text)
        rendered: list[str] = []
        for segment in segments:
            if not segment:
                continue
            if segment.startswith("`") and segment.endswith("`") and len(segment) >= 2:
                rendered.append(f"<code>{escape(segment[1:-1])}</code>")
                continue
            escaped_segment = escape(segment)
            escaped_segment = re.sub(
                r"\*\*(.+?)\*\*",
                lambda match: f"<strong>{match.group(1)}</strong>",
                escaped_segment,
            )
            escaped_segment = re.sub(
                r"\*(.+?)\*",
                lambda match: f"<em>{match.group(1)}</em>",
                escaped_segment,
            )
            rendered.append(escaped_segment)
        return "".join(rendered)
