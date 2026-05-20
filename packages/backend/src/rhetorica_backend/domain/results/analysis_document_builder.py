from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from rhetorica_backend.contracts.analysis_document import (
    AnalysisBlock,
    AnalysisCaveatBlock,
    AnalysisChartBlock,
    AnalysisDividerBlock,
    AnalysisDocument,
    AnalysisDocumentProvenance,
    AnalysisFindingBlock,
    AnalysisMetricGridBlock,
    AnalysisMetricItem,
    AnalysisNarrativeNoteBlock,
    AnalysisSectionHeaderBlock,
    AnalysisTableBlock,
)
from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.contracts.visualization import (
    VisualizationKind,
    VisualizationSpec,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner

_HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
_THINKING_TAG_PATTERN = re.compile(
    r"<(think|thinking)>(.*?)</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class _MarkdownSection:
    level: int
    title: str
    body: str


class AnalysisDocumentEligibilityPolicy:
    """Decides whether an answer has enough analytical substance for a report."""

    _ANALYTICAL_BLOCK_KINDS = frozenset({"chart", "table", "metric_grid"})

    def can_build(
        self,
        *,
        llm_candidate: dict[str, Any] | None,
        executions: list[ExecuteSqlResponse],
        fallback_visualization: VisualizationSpec | None,
    ) -> bool:
        return (
            bool(executions)
            or fallback_visualization is not None
            or self._candidate_has_analytical_blocks(llm_candidate)
        )

    def can_build_fallback(
        self,
        *,
        executions: list[ExecuteSqlResponse],
        fallback_visualization: VisualizationSpec | None,
    ) -> bool:
        return bool(executions) or fallback_visualization is not None

    def _candidate_has_analytical_blocks(
        self,
        candidate: dict[str, Any] | None,
    ) -> bool:
        if candidate is None:
            return False
        blocks = candidate.get("blocks")
        if not isinstance(blocks, list):
            return False
        return any(
            isinstance(block, dict)
            and block.get("kind") in self._ANALYTICAL_BLOCK_KINDS
            for block in blocks
        )


class AnalysisDocumentBuilder:
    """Builds and normalizes report-like analysis documents."""

    def __init__(
        self,
        visualization_planner: VisualizationPlanner | None = None,
        eligibility_policy: AnalysisDocumentEligibilityPolicy | None = None,
    ):
        self._visualization_planner = visualization_planner or VisualizationPlanner()
        self._eligibility_policy = eligibility_policy or AnalysisDocumentEligibilityPolicy()

    def build(
        self,
        *,
        answer: str,
        execution: ExecuteSqlResponse | None,
        executions: list[ExecuteSqlResponse] | None = None,
        llm_candidate: dict[str, Any] | None,
        fallback_visualization: VisualizationSpec | None,
        warnings: list[str],
    ) -> AnalysisDocument | None:
        sanitized_answer = _strip_thinking_tags(answer)
        sanitized_candidate = (
            _strip_thinking_tags_from_payload(llm_candidate)
            if llm_candidate is not None
            else None
        )
        execution_pool = _dedupe_executions([*(executions or []), *([execution] if execution else [])])
        primary_execution = execution_pool[-1] if execution_pool else execution

        if not self._eligibility_policy.can_build(
            llm_candidate=sanitized_candidate,
            executions=execution_pool,
            fallback_visualization=fallback_visualization,
        ):
            return None

        if sanitized_candidate:
            document = self._from_candidate(
                candidate=sanitized_candidate,
                answer=sanitized_answer,
                execution=primary_execution,
                executions=execution_pool,
                fallback_visualization=fallback_visualization,
                warnings=warnings,
            )
            if document is not None:
                return document

        if not self._eligibility_policy.can_build_fallback(
            executions=execution_pool,
            fallback_visualization=fallback_visualization,
        ):
            return None

        return self._build_fallback_document(
            answer=sanitized_answer,
            execution=primary_execution,
            executions=execution_pool,
            visualization=fallback_visualization,
            warnings=warnings,
        )

    @staticmethod
    def primary_visualization(
        document: AnalysisDocument | None,
    ) -> VisualizationSpec | None:
        if document is None:
            return None
        for block in document.blocks:
            if isinstance(block, AnalysisChartBlock):
                return block.visualization
        return None

    def _from_candidate(
        self,
        *,
        candidate: dict[str, Any],
        answer: str,
        execution: ExecuteSqlResponse | None,
        executions: list[ExecuteSqlResponse],
        fallback_visualization: VisualizationSpec | None,
        warnings: list[str],
    ) -> AnalysisDocument | None:
        normalized_blocks: list[dict[str, Any]] = []
        chart_or_table_blocks = [
            raw_block
            for raw_block in candidate.get("blocks", [])
            if isinstance(raw_block, dict)
            and raw_block.get("kind") in {"chart", "table"}
        ]
        sequential_fallback = len(chart_or_table_blocks) > 1
        block_execution_cursor = 0

        for raw_block in candidate.get("blocks", []):
            if not isinstance(raw_block, dict):
                continue
            resolved_execution = self._resolve_block_execution(
                raw_block=raw_block,
                executions=executions,
                default_execution=execution,
                sequential_fallback=sequential_fallback,
                fallback_index=block_execution_cursor,
            )
            normalized_block = self._normalize_block_candidate(
                raw_block=raw_block,
                execution=resolved_execution,
                fallback_visualization=fallback_visualization,
            )
            if normalized_block is None:
                continue
            normalized_blocks.append(normalized_block)
            if (
                sequential_fallback
                and raw_block.get("kind") in {"chart", "table"}
                and _optional_str(raw_block.get("source_execution_id")) is None
            ):
                block_execution_cursor += 1

        if not normalized_blocks:
            return None

        payload = {
            "document_version": "v1",
            "title": candidate.get("title") or "Analysis report",
            "executive_summary": candidate.get("executive_summary") or answer.strip() or None,
            "blocks": normalized_blocks,
            "warnings": _dedupe_strings(
                [*warnings, *_string_list(candidate.get("warnings"))]
            ),
            "provenance": {
                "source_type": "chat_answer",
                "execution_id": execution.execution_id if execution else None,
                "planner_mode": self._resolve_document_planner_mode(normalized_blocks),
            },
        }

        try:
            return AnalysisDocument.model_validate(payload)
        except ValidationError:
            return None

    def _normalize_block_candidate(
        self,
        *,
        raw_block: dict[str, Any],
        execution: ExecuteSqlResponse | None,
        fallback_visualization: VisualizationSpec | None,
    ) -> dict[str, Any] | None:
        kind = raw_block.get("kind")
        if kind == "chart":
            raw_visualization = raw_block.get("visualization")
            visualization = self._coerce_visualization(
                candidate=raw_visualization if isinstance(raw_visualization, dict) else None,
                execution=execution,
                fallback_visualization=fallback_visualization,
            )
            if visualization is None:
                return None
            return {
                "kind": "chart",
                "title": raw_block.get("title"),
                "description": raw_block.get("description"),
                "insight": raw_block.get("insight"),
                "source_execution_id": execution.execution_id if execution else None,
                "preview": (
                    execution.preview.model_dump(mode="json")
                    if execution is not None
                    else None
                ),
                "visualization": visualization.model_dump(mode="json"),
            }
        if kind == "metric_grid":
            items = []
            for item in raw_block.get("items", []):
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                value = str(item.get("value") or "").strip()
                if not label or not value:
                    continue
                items.append(
                    AnalysisMetricItem(
                        label=label,
                        value=value,
                        context=_optional_str(item.get("context")),
                        delta=_optional_str(item.get("delta")),
                        tone=item.get("tone")
                        if item.get("tone") in {
                            "neutral",
                            "positive",
                            "warning",
                            "negative",
                        }
                        else "neutral",
                    ).model_dump(mode="json")
                )
            if not items:
                return None
            return {
                "kind": "metric_grid",
                "title": raw_block.get("title"),
                "description": raw_block.get("description"),
                "items": items,
            }
        if kind == "finding":
            body = _optional_str(raw_block.get("body"))
            title = _optional_str(raw_block.get("title"))
            if not body or not title:
                return None
            return {
                "kind": "finding",
                "title": title,
                "body": body,
                "emphasis": raw_block.get("emphasis")
                if raw_block.get("emphasis") in {"info", "important", "critical"}
                else "important",
            }
        if kind == "narrative_note":
            markdown = _optional_str(raw_block.get("markdown")) or _optional_str(
                raw_block.get("body")
            )
            if not markdown:
                return None
            return {
                "kind": "narrative_note",
                "title": raw_block.get("title"),
                "markdown": markdown,
            }
        if kind == "caveat":
            body = _optional_str(raw_block.get("body"))
            title = _optional_str(raw_block.get("title"))
            if not body or not title:
                return None
            return {
                "kind": "caveat",
                "title": title,
                "body": body,
            }
        if kind == "table":
            columns = _string_list(raw_block.get("columns"))
            return {
                "kind": "table",
                "title": raw_block.get("title"),
                "description": raw_block.get("description"),
                "source": "execution_preview",
                "source_execution_id": execution.execution_id if execution else None,
                "preview": (
                    execution.preview.model_dump(mode="json")
                    if execution is not None
                    else None
                ),
                "columns": columns or (list(execution.preview.columns) if execution else []),
                "preferred_hidden": bool(raw_block.get("preferred_hidden", True)),
                "action_label": raw_block.get("action_label") or "Show rows",
            }
        if kind == "section_header":
            title = _optional_str(raw_block.get("title"))
            if not title:
                return None
            return {
                "kind": "section_header",
                "title": title,
                "description": raw_block.get("description"),
            }
        if kind == "divider":
            return {"kind": "divider"}
        return None

    def _build_fallback_document(
        self,
        *,
        answer: str,
        execution: ExecuteSqlResponse | None,
        executions: list[ExecuteSqlResponse],
        visualization: VisualizationSpec | None,
        warnings: list[str],
    ) -> AnalysisDocument:
        if len(executions) > 1:
            return self._build_multi_execution_document(
                answer=answer,
                executions=executions,
                warnings=warnings,
            )

        blocks: list[AnalysisBlock] = []
        summary = answer.strip() or "Result prepared from the latest assistant answer."

        if visualization is not None:
            blocks.append(
                AnalysisChartBlock(
                    title=visualization.title,
                    description=visualization.description,
                    insight=visualization.summary,
                    source_execution_id=execution.execution_id if execution else None,
                    preview=execution.preview if execution else None,
                    visualization=visualization,
                )
            )

        metrics = self._build_metric_grid(execution)
        if metrics is not None:
            blocks.append(metrics)

        if summary:
            blocks.append(AnalysisFindingBlock(title="Key takeaway", body=summary))

        if warnings:
            blocks.append(
                AnalysisCaveatBlock(
                    title="Warnings",
                    body="; ".join(_dedupe_strings(warnings)),
                )
            )

        if execution and execution.preview.columns:
            blocks.append(
                AnalysisTableBlock(
                    title="Preview rows",
                    description="Execution preview is available as drilldown.",
                    source_execution_id=execution.execution_id,
                    preview=execution.preview,
                    columns=list(execution.preview.columns),
                    preferred_hidden=visualization is not None,
                    action_label="Show rows" if visualization is not None else "Open result table",
                )
            )

        planner_mode = _planner_mode_from_set(
            {visualization.planner_mode} if visualization is not None else set()
        )
        return AnalysisDocument(
            title=visualization.title if visualization and visualization.title else "Analysis report",
            executive_summary=summary,
            blocks=blocks,
            warnings=_dedupe_strings(warnings),
            provenance=AnalysisDocumentProvenance(
                execution_id=execution.execution_id if execution else None,
                planner_mode=planner_mode,
            ),
        )

    def _build_multi_execution_document(
        self,
        *,
        answer: str,
        executions: list[ExecuteSqlResponse],
        warnings: list[str],
    ) -> AnalysisDocument:
        sections = _markdown_sections(answer)
        executive_summary = _resolve_executive_summary(answer, sections)
        analytic_sections = [
            section
            for section in sections
            if section.level >= 3 and not _is_summary_heading(section.title)
        ]
        selected_executions = executions[:6]
        blocks: list[AnalysisBlock] = []
        planner_modes: set[str] = set()

        if executive_summary:
            blocks.append(
                AnalysisFindingBlock(
                    title="Key takeaway",
                    body=executive_summary,
                )
            )

        for index, execution in enumerate(selected_executions):
            section = analytic_sections[index] if index < len(analytic_sections) else None
            section_title = section.title if section is not None else None
            note_markdown = _section_note_markdown(section)
            insight = _extract_section_insight(section.body if section is not None else "")

            if section_title:
                blocks.append(
                    AnalysisSectionHeaderBlock(
                        title=section_title,
                        description=None if note_markdown else insight,
                    )
                )

            if note_markdown:
                blocks.append(AnalysisNarrativeNoteBlock(markdown=note_markdown))

            visualization = self._visualization_planner.plan(execution=execution)
            if visualization is not None and visualization.kind != VisualizationKind.TABLE:
                planner_modes.add(visualization.planner_mode)
                visualization = visualization.model_copy(
                    update={
                        "title": section_title or visualization.title,
                        "summary": insight or visualization.summary,
                    }
                )
                blocks.append(
                    AnalysisChartBlock(
                        title=section_title or visualization.title,
                        description=visualization.description,
                        insight=insight or visualization.summary,
                        source_execution_id=execution.execution_id,
                        preview=execution.preview,
                        visualization=visualization,
                    )
                )

            if execution.preview.columns:
                blocks.append(
                    AnalysisTableBlock(
                        title=f"{section_title or 'Drilldown'} rows",
                        description="Preview rows for this slice stay available as drilldown.",
                        source_execution_id=execution.execution_id,
                        preview=execution.preview,
                        columns=list(execution.preview.columns),
                        preferred_hidden=True,
                        action_label="Show rows",
                    )
                )

            if index < len(selected_executions) - 1:
                blocks.append(AnalysisDividerBlock())

        if not blocks:
            latest_execution = selected_executions[-1] if selected_executions else None
            latest_visualization = (
                self._visualization_planner.plan(execution=latest_execution)
                if latest_execution is not None
                else None
            )
            return self._build_fallback_document(
                answer=answer,
                execution=latest_execution,
                executions=[latest_execution] if latest_execution is not None else [],
                visualization=latest_visualization,
                warnings=warnings,
            )

        if warnings:
            blocks.append(
                AnalysisCaveatBlock(
                    title="Warnings",
                    body="; ".join(_dedupe_strings(warnings)),
                )
            )

        return AnalysisDocument(
            title=_resolve_report_title(sections) or "Analysis report",
            executive_summary=executive_summary,
            blocks=blocks,
            warnings=_dedupe_strings(warnings),
            provenance=AnalysisDocumentProvenance(
                execution_id=selected_executions[-1].execution_id if selected_executions else None,
                planner_mode=_planner_mode_from_set(planner_modes),
            ),
        )

    def _coerce_visualization(
        self,
        *,
        candidate: dict[str, Any] | None,
        execution: ExecuteSqlResponse | None,
        fallback_visualization: VisualizationSpec | None,
    ) -> VisualizationSpec | None:
        if execution is None:
            return fallback_visualization
        planned = self._visualization_planner.plan(
            execution=execution,
            llm_candidate=candidate,
        )
        return planned or fallback_visualization

    def _resolve_block_execution(
        self,
        *,
        raw_block: dict[str, Any],
        executions: list[ExecuteSqlResponse],
        default_execution: ExecuteSqlResponse | None,
        sequential_fallback: bool,
        fallback_index: int,
    ) -> ExecuteSqlResponse | None:
        reference = _optional_str(raw_block.get("source_execution_id"))
        if reference is not None:
            for item in executions:
                if item.execution_id == reference:
                    return item
        if sequential_fallback and executions:
            return executions[min(fallback_index, len(executions) - 1)]
        if default_execution is not None:
            return default_execution
        if executions:
            return executions[-1]
        return None

    @staticmethod
    def _resolve_document_planner_mode(
        blocks: list[dict[str, Any]],
    ) -> str:
        chart_modes = {
            block["visualization"].get("planner_mode")
            for block in blocks
            if block.get("kind") == "chart"
            and isinstance(block.get("visualization"), dict)
            and isinstance(block["visualization"].get("planner_mode"), str)
        }
        return _planner_mode_from_set(chart_modes)

    @staticmethod
    def _build_metric_grid(
        execution: ExecuteSqlResponse | None,
    ) -> AnalysisMetricGridBlock | None:
        if execution is None:
            return None
        items = [
            AnalysisMetricItem(label="Status", value=execution.status),
            AnalysisMetricItem(
                label="Rows",
                value=str(
                    execution.row_count
                    if execution.row_count is not None
                    else len(execution.preview.rows)
                ),
            ),
        ]
        if execution.duration_ms is not None:
            items.append(
                AnalysisMetricItem(
                    label="Latency",
                    value=f"{execution.duration_ms} ms",
                )
            )
        return AnalysisMetricGridBlock(
            title="Execution summary",
            description="Fast sanity metrics for the latest guarded query.",
            items=items,
        )


def primary_visualization_from_document(
    document: AnalysisDocument | None,
) -> VisualizationSpec | None:
    return AnalysisDocumentBuilder.primary_visualization(document)


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        normalized = _optional_str(item)
        if normalized is not None:
            result.append(normalized)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_executions(
    executions: list[ExecuteSqlResponse],
) -> list[ExecuteSqlResponse]:
    seen: set[str] = set()
    result: list[ExecuteSqlResponse] = []
    for execution in executions:
        execution_id = execution.execution_id
        if execution_id in seen:
            continue
        seen.add(execution_id)
        result.append(execution)
    return result


def _markdown_sections(markdown: str) -> list[_MarkdownSection]:
    sections: list[_MarkdownSection] = []
    current_level: int | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        match = _HEADING_PATTERN.match(line.strip())
        if match is None:
            if current_title is not None:
                current_lines.append(line)
            continue
        if current_title is not None:
            sections.append(
                _MarkdownSection(
                    level=current_level or 2,
                    title=current_title,
                    body="\n".join(current_lines).strip(),
                )
            )
        current_level = len(match.group(1))
        current_title = _clean_heading_title(match.group(2))
        current_lines = []

    if current_title is not None:
        sections.append(
            _MarkdownSection(
                level=current_level or 2,
                title=current_title,
                body="\n".join(current_lines).strip(),
            )
        )
    return sections


def _clean_heading_title(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"`([^`]+)`", r"\1", normalized)
    normalized = normalized.strip("`")
    normalized = re.sub(r"^\*+(.*?)\*+$", r"\1", normalized)
    normalized = re.sub(r"^[^\wА-Яа-яЁё]+", "", normalized)
    normalized = re.sub(r"^\d+[\.\)]\s*", "", normalized)
    return normalized.strip()


def _is_summary_heading(value: str) -> bool:
    normalized = value.casefold()
    return "ключев" in normalized or "вывод" in normalized or "summary" in normalized


def _resolve_report_title(sections: list[_MarkdownSection]) -> str | None:
    for section in sections:
        if section.level <= 2 and not _is_summary_heading(section.title):
            return section.title
    return None


def _resolve_executive_summary(
    answer: str,
    sections: list[_MarkdownSection],
) -> str | None:
    for section in sections:
        if not _is_summary_heading(section.title):
            continue
        cleaned = _strip_markdown_tables(section.body).strip()
        if cleaned:
            return cleaned

    cleaned_answer = _strip_markdown_tables(answer)
    cleaned_lines = [
        line
        for line in cleaned_answer.splitlines()
        if not _HEADING_PATTERN.match(line.strip())
    ]
    cleaned = "\n".join(cleaned_lines).strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if paragraphs:
        return paragraphs[0]
    normalized_answer = answer.strip()
    return normalized_answer or None


def _strip_markdown_tables(markdown: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("|"):
            continue
        cleaned_lines.append(raw_line.rstrip())
    return "\n".join(cleaned_lines).strip()


def _strip_thinking_tags(text: str) -> str:
    return _THINKING_TAG_PATTERN.sub(" ", text)


def _strip_thinking_tags_from_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_thinking_tags(value)
    if isinstance(value, list):
        return [_strip_thinking_tags_from_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_thinking_tags_from_payload(item)
            for key, item in value.items()
        }
    return value


def _section_note_markdown(section: _MarkdownSection | None) -> str | None:
    if section is None:
        return None
    cleaned = _strip_markdown_tables(section.body).strip()
    return cleaned or None


def _extract_section_insight(markdown: str) -> str | None:
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = re.sub(r"^\*+|\*+$", "", line).strip()
        if ":" not in normalized:
            continue
        label, value = normalized.split(":", 1)
        if "инсайт" in label.casefold() or "insight" in label.casefold():
            result = value.strip()
            return result or None
    return None


def _planner_mode_from_set(values: set[str]) -> str:
    VALID_MODES = {"rule_fallback", "llm_validated", "mixed"}

    if not values:
        return "rule_fallback"

    # Normalize any invalid modes (e.g., "table_fallback") to "rule_fallback"
    normalized = {v if v in VALID_MODES else "rule_fallback" for v in values}

    if len(normalized) == 1:
        return next(iter(normalized))
    return "mixed"
