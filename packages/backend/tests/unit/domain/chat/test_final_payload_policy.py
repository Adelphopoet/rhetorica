from __future__ import annotations

import json

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)

_VALID_VISUALIZATION = {
    "version": "v2",
    "kind": "line",
    "data_source": {
        "execution_id": "exec-1",
        "row_count": 5,
        "is_truncated": False,
    },
    "encoding": {
        "x": "report_date",
        "y": ["orders_count"],
    },
    "transform": {
        "limit": 50,
        "aggregation": "none",
        "normalize": "none",
        "cumulative": False,
    },
    "rationale": {
        "why_kind": "Линейный график подходит для дневного тренда.",
        "why_fields": ["report_date", "orders_count"],
    },
}


def test_preserve_raw_markdown_answer_does_not_override_explicit_visualization() -> None:
    payload = FinalPayloadPolicy.preserve_raw_markdown_answer(
        raw_final_text=(
            "## Orders by day\n\n"
            "```text\n"
            "600 ┤\n"
            "550 ┤   █\n"
            "```\n"
        ),
        parsed_payload={
            "answer": "Показал количество заказов по дням за май 2026.",
            "answer_markdown": "Показал количество заказов по дням за май 2026.",
            "visualization": _VALID_VISUALIZATION,
        },
    )

    assert payload["answer"] == "Показал количество заказов по дням за май 2026."
    assert payload["answer_markdown"] == "Показал количество заказов по дням за май 2026."


def test_preserve_raw_markdown_table_even_with_explicit_visualization() -> None:
    raw_answer = (
        "## Топ товарных групп\n\n"
        "| Группа | Выручка |\n"
        "| --- | ---: |\n"
        "| Молочная продукция | 3040.5 |\n"
        "| Уход и гигиена | 1932.49 |"
    )

    payload = FinalPayloadPolicy.preserve_raw_markdown_answer(
        raw_final_text=raw_answer,
        parsed_payload={
            "answer": "## Топ товарных групп",
            "answer_markdown": "## Топ товарных групп",
            "visualization": _VALID_VISUALIZATION,
        },
    )

    assert payload["answer"] == raw_answer
    assert payload["answer_markdown"] == raw_answer


def test_preserve_raw_markdown_answer_from_payload_restores_json_answer_markdown() -> None:
    raw_answer = (
        "## Найдена таблица\n\n"
        "### Основная таблица: `art_price`\n\n"
        "| Колонка | Тип |\n"
        "| --- | --- |\n"
        "| `art_code` | VARCHAR |\n\n"
        "**Доступные коннекторы:**\n"
        "- **Trino**: `trino_omni.iceberg.dm_ft.art_price`"
    )
    collapsed_answer = (
        "## Найдена таблица ### Основная таблица: `art_price` "
        "| Колонка | Тип | | --- | --- | | `art_code` | VARCHAR | "
        "**Доступные коннекторы:** - **Trino**: `trino_omni.iceberg.dm_ft.art_price`"
    )

    payload = FinalPayloadPolicy.preserve_raw_markdown_answer_from_payload(
        raw_final_text=json.dumps(
            {"schema_version": "v1", "answer_markdown": raw_answer},
            ensure_ascii=False,
        ),
        parsed_raw_payload={"schema_version": "v1", "answer_markdown": raw_answer},
        parsed_payload={
            "answer": collapsed_answer,
            "answer_markdown": collapsed_answer,
        },
    )

    assert payload["answer"] == raw_answer
    assert payload["answer_markdown"] == raw_answer


def test_runtime_execution_backed_answer_accepts_generic_execution_reference() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(columns=["date", "order_count"], rows=[["2026-05-01", 10]]),
    )
    state = ToolOrchestrationState(
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT date, order_count FROM dm.orders",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    assert FinalPayloadPolicy.has_runtime_execution_backed_answer(
        llm_payload={
            "claims": [{"claim_id": "c1", "text": "Есть данные", "status": "verified"}],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "type": "execution_result",
                    "reference": "execution",
                    "status": "verified",
                }
            ],
            "claim_evidence_links": [{"claim_id": "c1", "evidence_ids": ["e1"]}],
        },
        state=state,
    )


def test_accepts_successful_jira_create_answer_with_issue_key() -> None:
    state = ToolOrchestrationState(tool_call_count=1)
    state.record_tool_result(
        tool_name="create_jira_issue",
        arguments={
            "summary": "тест",
            "description": "тест",
            "request_kind": "task",
        },
        result={
            "ok": True,
            "issue_key": "DWH-123",
            "url": "https://jira.example/browse/DWH-123",
            "project_key": "DWH",
        },
    )

    assert FinalPayloadPolicy.is_acceptable_final_payload(
        llm_payload={
            "answer": "Создана задача DWH-123.",
            "answer_markdown": "Создана задача DWH-123.",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        state=state,
        request=ChatAskRequest(question="создай заявку в Jira"),
    )


def test_rejects_jira_create_answer_without_created_issue_key() -> None:
    state = ToolOrchestrationState(tool_call_count=1)
    state.record_tool_result(
        tool_name="create_jira_issue",
        arguments={
            "summary": "тест",
            "description": "тест",
            "request_kind": "task",
        },
        result={
            "ok": True,
            "issue_key": "DWH-123",
            "url": "https://jira.example/browse/DWH-123",
            "project_key": "DWH",
        },
    )

    assert not FinalPayloadPolicy.is_acceptable_final_payload(
        llm_payload={
            "answer": "Создана задача.",
            "answer_markdown": "Создана задача.",
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
        },
        state=state,
        request=ChatAskRequest(question="создай заявку в Jira"),
    )
