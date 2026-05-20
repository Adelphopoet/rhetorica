from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.results.analysis_document_builder import (
    AnalysisDocumentBuilder,
)
from rhetorica_backend.domain.results.visualization_planner import VisualizationPlanner


def _execution(
    execution_id: str,
    columns: list[str],
    rows: list[list[Any]],
) -> ExecuteSqlResponse:
    return ExecuteSqlResponse(
        execution_id=execution_id,
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
        row_count=len(rows),
        warnings=[],
        violations=[],
        preview=QueryPreview(columns=columns, rows=rows),
    )


def test_builder_creates_multi_execution_report_from_markdown_sections() -> None:
    """Without llm_candidate, no chart blocks are created - only TABLE blocks for drilldown."""
    builder = AnalysisDocumentBuilder()
    first = _execution(
        "exec-1",
        ["delivery_type", "total_sales"],
        [["DELIVERY_EXPRESS", 33200000000], ["DELIVERY_SELF", 7970000000]],
    )
    second = _execution(
        "exec-2",
        ["city_id", "total_sales"],
        [[2878, 2570000000], [3338, 2030000000]],
    )

    document = builder.build(
        answer=(
            "## Финансовый отчёт\n\n"
            "### 1. Тип доставки\n\n"
            "| delivery_type | total_sales |\n"
            "| --- | --- |\n"
            "| DELIVERY_EXPRESS | 33.2 млрд |\n\n"
            "**Инсайт:** Экспресс-доставка доминирует.\n\n"
            "### 2. Топ-городов\n\n"
            "| city_id | total_sales |\n"
            "| --- | --- |\n"
            "| 2878 | 2.57 млрд |\n\n"
            "**Инсайт:** Топ-3 города формируют основную выручку.\n\n"
            "## Ключевые выводы\n\n"
            "1. Экспресс-доставка дает основной оборот.\n"
            "2. Концентрация по городам высокая.\n"
        ),
        execution=second,
        executions=[first, second],
        llm_candidate=None,
        fallback_visualization=None,
        warnings=[],
    )

    assert document is not None
    assert document.title == "Финансовый отчёт"
    assert "Экспресс-доставка" in (document.executive_summary or "")

    chart_blocks = [block for block in document.blocks if block.kind == "chart"]
    finding_blocks = [block for block in document.blocks if block.kind == "finding"]
    table_blocks = [block for block in document.blocks if block.kind == "table"]

    assert len(finding_blocks) == 1
    # Without llm_candidate, no chart blocks are created (opt-in charts)
    assert len(chart_blocks) == 0
    # Table blocks are still created for drilldown (one per execution)
    assert len(table_blocks) == 2
    # Verify table blocks contain the expected data
    assert table_blocks[0].source_execution_id == "exec-1"
    assert table_blocks[0].preview.columns == ["delivery_type", "total_sales"]
    assert table_blocks[1].source_execution_id == "exec-2"
    assert table_blocks[1].preview is not None
    assert table_blocks[1].preview.rows[0][0] == 2878


def test_builder_preserves_underscores_in_markdown_section_titles() -> None:
    builder = AnalysisDocumentBuilder()
    execution = _execution(
        "exec-last-mile",
        ["order_id", "sale_amt"],
        [[1, 7647.0]],
    )
    second_execution = _execution(
        "exec-art-sber",
        ["order_id", "sale_amt"],
        [[2, 996651.0]],
    )

    document = builder.build(
        answer=(
            "## Examples\n\n"
            "### `ft_order_last_mile` (Last Mile доставка)\n\n"
            "**Инсайт:** Peak sale amount is visible.\n\n"
            "### ft_order_art_sber (СберМаркет)\n\n"
            "**Инсайт:** Peak warehouse code is visible.\n"
        ),
        execution=second_execution,
        executions=[execution, second_execution],
        llm_candidate=None,
        fallback_visualization=None,
        warnings=[],
    )

    assert document is not None
    section_headers = [block for block in document.blocks if block.kind == "section_header"]
    assert section_headers[0].title == "ft_order_last_mile (Last Mile доставка)"


def test_builder_does_not_create_report_for_plain_conversation() -> None:
    builder = AnalysisDocumentBuilder()

    document = builder.build(
        answer="Привет! Чем могу помочь?",
        execution=None,
        executions=[],
        llm_candidate={
            "title": "Analysis report",
            "executive_summary": "Привет! Чем могу помочь?",
            "blocks": [
                {
                    "kind": "finding",
                    "title": "Key takeaway",
                    "body": "Привет! Чем могу помочь?",
                },
                {
                    "kind": "caveat",
                    "title": "Warnings",
                    "body": "llm_finalizer_requested",
                },
            ],
            "warnings": ["llm_finalizer_requested"],
        },
        fallback_visualization=None,
        warnings=["llm_finalizer_requested"],
    )

    assert document is None


def test_builder_keeps_report_for_analytical_candidate_without_sql() -> None:
    builder = AnalysisDocumentBuilder()

    document = builder.build(
        answer="GMV вырос.",
        execution=None,
        executions=[],
        llm_candidate={
            "title": "GMV snapshot",
            "executive_summary": "GMV вырос.",
            "blocks": [
                {
                    "kind": "metric_grid",
                    "title": "KPI",
                    "items": [
                        {
                            "label": "GMV",
                            "value": "$148K",
                            "delta": "+23%",
                            "tone": "positive",
                        }
                    ],
                }
            ],
        },
        fallback_visualization=None,
        warnings=[],
    )

    assert document is not None
    assert document.title == "GMV snapshot"
    assert [block.kind for block in document.blocks] == ["metric_grid"]


def test_builder_uses_rule_fallback_provenance_without_llm_visualization() -> None:
    builder = AnalysisDocumentBuilder()
    execution = _execution(
        "exec-table-fallback",
        ["order_id", "amount"],
        [[1, 100.0], [2, 250.0]],
    )
    fallback_visualization = VisualizationPlanner().plan(execution=execution)

    assert fallback_visualization is None

    document = builder.build(
        answer="Показал 10 строк из таблицы.",
        execution=execution,
        executions=[execution],
        llm_candidate=None,
        fallback_visualization=fallback_visualization,
        warnings=[],
    )

    assert document is not None
    assert document.provenance.planner_mode == "rule_fallback"


def test_builder_strips_thinking_tags_from_candidate_document() -> None:
    builder = AnalysisDocumentBuilder()
    execution = _execution(
        "exec-think",
        ["report_date", "orders"],
        [["2026-05-01", 10]],
    )

    document = builder.build(
        answer="<think>hidden</think>\nVisible answer",
        execution=execution,
        executions=[execution],
        llm_candidate={
            "title": "Analysis report",
            "executive_summary": "<think>internal</think> Summary",
            "blocks": [
                {
                    "kind": "finding",
                    "title": "Key takeaway",
                    "body": "<think>debug</think> Body",
                }
            ],
        },
        fallback_visualization=None,
        warnings=[],
    )

    assert document is not None
    assert "<think>" not in (document.executive_summary or "")
    assert document.blocks[0].kind == "finding"
    assert "<think>" not in document.blocks[0].body
