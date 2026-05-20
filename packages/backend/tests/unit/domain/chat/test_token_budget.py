from __future__ import annotations

import json

from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactSerializer,
    PromptContextBudgetPolicy,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)


def test_unknown_model_uses_conservative_window_and_warning() -> None:
    service = LlmPromptBudgetService(model_name="local/unknown-model")

    assert service.context_window_tokens() == 16_384
    assert "context_window_unconfirmed" in service.diagnostics_warnings()


def test_prompt_budget_caps_oversized_response_reserve() -> None:
    service = LlmPromptBudgetService(
        model_name="local/unknown-model",
        context_window_tokens=16_384,
        response_reserve_tokens=32_000,
    )

    assert service.prompt_budget_tokens() == 12_288
    assert "response_reserve_capped" in service.diagnostics_warnings()


def test_prompt_budget_counts_tools_and_trims_old_history() -> None:
    service = LlmPromptBudgetService(
        model_name="local/unknown-model",
        context_window_tokens=512,
        response_reserve_tokens=0,
    )
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "old question " + ("x" * 6_000)},
        {"role": "assistant", "content": "old answer " + ("y" * 6_000)},
        {"role": "user", "content": "current question"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_metadata_catalog",
                "description": "tool " + ("z" * 1_500),
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    result = service.fit_request_messages(messages=messages, tools=tools)

    assert result.messages[0] == messages[0]
    assert result.messages[-1] == messages[-1]
    assert len(result.messages) < len(messages)
    assert "context_truncated" in result.warnings


def test_prompt_budget_compacts_tool_schemas_before_dropping_context() -> None:
    service = LlmPromptBudgetService(
        model_name="local/unknown-model",
        context_window_tokens=900,
        response_reserve_tokens=100,
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current question"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": "x" * 2_000,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "y" * 2_000,
                        }
                    },
                    "required": ["sql"],
                },
            },
        }
    ]

    result = service.fit_request_messages(messages=messages, tools=tools)

    assert result.messages == messages
    assert result.tools is not None
    assert result.tools[0]["function"]["name"] == "run_sql"
    assert "description" not in result.tools[0]["function"]
    assert "tool_schemas_compacted" in result.warnings
    assert "context_budget_exceeded" not in result.warnings


def test_tool_result_serializer_limits_large_payload_for_llm_only() -> None:
    serializer = ToolResultPromptSerializer()
    full_result = {
        "ok": True,
        "execution_id": "exec-1",
        "rows": [{"id": index, "payload": "x" * 500} for index in range(150)],
        "metadata_sources": ["openmetadata"],
    }

    payload = serializer.serialize(tool_name="run_sql", tool_result=full_result)
    parsed = json.loads(payload)

    assert len(payload) < 40_500
    assert parsed["artifact_type"] == "evidence_summary"
    run_sql = parsed["compact_payload"]["run_sql"]
    assert run_sql["execution_id"] == "exec-1"
    assert run_sql["rows_truncated"] is True
    assert run_sql["prompt_rows_returned"] <= 100
    assert parsed["truncated"] is True


def test_prompt_artifact_serializer_omits_large_worker_transcript() -> None:
    serializer = PromptArtifactSerializer()
    raw_transcript = "RAW_WORKER_TRANSCRIPT:" + ("x" * 20_000)

    envelope = serializer.serialize(
        artifact_type="worker_result",
        artifact={
            "task_id": "worker-1",
            "goal": "Check GMV drop",
            "status": "done",
            "key_finding": "GMV dropped in segment A.",
            "evidence_ids": ["exec-1"],
            "raw_transcript": raw_transcript,
            "tool_results": [{"payload": raw_transcript}],
        },
    )
    payload = envelope.stable_json()

    assert "RAW_WORKER_TRANSCRIPT" not in payload
    assert envelope.compact_payload == {
        "evidence_ids": ["exec-1"],
        "goal": "Check GMV drop",
        "key_finding": "GMV dropped in segment A.",
        "status": "done",
        "task_id": "worker-1",
    }
    assert envelope.warnings == ["prompt_worker_result_raw_payload_omitted"]


def test_large_tool_payload_compacted_deterministically() -> None:
    serializer = ToolResultPromptSerializer()
    full_result = {
        "ok": True,
        "execution_id": "exec-2",
        "rows": [{"id": index, "payload": "row-" + ("x" * 2_000)} for index in range(150)],
        "debug_payload": "DEBUG:" + ("y" * 5_000),
    }

    first = serializer.serialize(tool_name="run_sql", tool_result=full_result)
    second = serializer.serialize(tool_name="run_sql", tool_result=full_result)
    parsed = json.loads(first)

    assert first == second
    assert "DEBUG:" not in first
    run_sql = parsed["compact_payload"]["run_sql"]
    assert run_sql["prompt_rows_returned"] < 100
    assert run_sql["rows_truncated"] is True
    assert parsed["truncation_flags"] == ["payload_truncated", "artifact_payload_truncated"]
    assert parsed["warnings"] == [
        "prompt_evidence_summary_raw_payload_omitted",
        "prompt_artifact_budget_warning",
    ]


def test_run_sql_nested_execution_preview_survives_prompt_artifact() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "generated_sql": "SELECT dt, revenue FROM dm.sales",
        "executed_sql": "SELECT dt, revenue FROM dm.sales",
        "optimization_trace": {"raw": "debug"},
        "execution": {
            "execution_id": "exec-3",
            "status": "succeeded",
            "row_count": 2,
            "preview": {
                "columns": ["dt", "revenue"],
                "rows": [["2026-05-01", "100.00"], ["2026-05-02", "120.00"]],
            },
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="run_sql", tool_result=tool_result))

    assert parsed["compact_payload"]["tool_name"] == "run_sql"
    run_sql = parsed["compact_payload"]["run_sql"]
    assert run_sql["ok"] is True
    assert run_sql["execution_id"] == "exec-3"
    assert run_sql["execution_status"] == "succeeded"
    assert run_sql["result_state"] == "has_rows"
    assert run_sql["row_count"] == 2
    assert run_sql["prompt_row_limit"] == 100
    assert run_sql["max_prompt_row_limit"] == 500
    assert run_sql["prompt_rows_returned"] == 2
    assert run_sql["retrieval_contract"] == "bounded_prompt_preview"
    assert run_sql["columns"] == ["dt", "revenue"]
    assert run_sql["rows"] == [
        ["2026-05-01", "100.00"],
        ["2026-05-02", "120.00"],
    ]
    assert "SELECT dt" not in json.dumps(parsed, ensure_ascii=False)
    assert "optimization_trace" not in json.dumps(parsed, ensure_ascii=False)


def test_run_sql_failed_execution_is_prompted_as_failed() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "execution": {
            "execution_id": "exec-failed",
            "status": "failed",
            "row_count": None,
            "preview": {"columns": [], "rows": []},
            "error_message": "very long raw error",
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="run_sql", tool_result=tool_result))

    run_sql = parsed["compact_payload"]["run_sql"]
    assert run_sql["ok"] is False
    assert run_sql["execution_status"] == "failed"
    assert run_sql["result_state"] == "failed"
    assert run_sql["error_message"] == "very long raw error"


def test_jira_create_result_keeps_issue_key_for_final_answer() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "issue_key": "DWH-123",
        "url": "https://jira.example/browse/DWH-123",
        "project_key": "DWH",
        "assignee": {"name": "analyst_user", "display_name": "Alex Analyst"},
        "issue_type": "Задача",
    }

    parsed = json.loads(
        serializer.serialize(tool_name="create_jira_issue", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "create_jira_issue"
    assert payload["ok"] is True
    assert payload["issue_key"] == "DWH-123"
    assert payload["url"] == "https://jira.example/browse/DWH-123"
    assert payload["project_key"] == "DWH"
    assert payload["assignee"]["name"] == "analyst_user"
    assert payload["issue_type"] == "Задача"


def test_run_sql_empty_success_is_explicit_empty_result() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "execution": {
            "execution_id": "exec-empty",
            "status": "succeeded",
            "row_count": 0,
            "preview": {"columns": ["dt", "revenue"], "rows": []},
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="run_sql", tool_result=tool_result))
    run_sql = parsed["compact_payload"]["run_sql"]

    assert run_sql["ok"] is True
    assert run_sql["row_count"] == 0
    assert run_sql["result_state"] == "empty_result"
    assert run_sql["rows"] == []
    assert run_sql["retrieval_contract"] == "bounded_prompt_preview"
    assert run_sql["rows_truncated"] is False


def test_run_sql_prompt_diagnostics_include_compact_row_counts() -> None:
    serializer = ToolResultPromptSerializer()
    envelope = serializer.serialize_envelope(
        tool_name="run_sql",
        tool_result={
            "ok": True,
            "execution": {
                "execution_id": "exec-many",
                "status": "succeeded",
                "row_count": 109,
                "preview": {
                    "columns": ["id"],
                    "rows": [[index] for index in range(109)],
                },
            },
        },
    )

    diagnostics = serializer.diagnostics(envelope=envelope)

    assert diagnostics.tool_name == "run_sql"
    assert diagnostics.original_row_count == 109
    assert diagnostics.rows_sent == 100
    assert diagnostics.rows_truncated is True
    assert diagnostics.outer_json_truncated is False


def test_metadata_tool_results_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "count": 2,
        "results": [
            {"name": "dm.sales_daily", "description": "daily sales", "debug": "raw"},
            {"name": "dm.orders", "description": "orders"},
        ],
        "raw_payload": "RAW_METADATA_PAYLOAD",
    }

    parsed = json.loads(
        serializer.serialize(tool_name="search_metadata_catalog", tool_result=tool_result)
    )

    assert parsed["compact_payload"]["tool_name"] == "search_metadata_catalog"
    assert parsed["compact_payload"]["count"] == 2
    assert parsed["compact_payload"]["results"][0] == {
        "name": "dm.sales_daily",
        "description": "daily sales",
    }
    assert "RAW_METADATA_PAYLOAD" not in json.dumps(parsed, ensure_ascii=False)
    assert parsed["warnings"] == ["prompt_evidence_summary_raw_payload_omitted"]


def test_metadata_entity_details_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "entity": {
            "entity": {
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.ft_order_window_revenue",
                "description": "Daily revenue by order window",
                "columns": [
                    {"name": "order_date", "dataType": "date"},
                    {"name": "net_revenue", "dataType": "decimal"},
                    {"name": "customer_id", "dataType": "string"},
                ],
            },
            "columnNames": ["order_date", "net_revenue", "customer_id"],
        },
    }

    parsed = json.loads(
        serializer.serialize(tool_name="get_metadata_entity_details", tool_result=tool_result)
    )

    entity = parsed["compact_payload"]["entity"]
    assert parsed["compact_payload"]["tool_name"] == "get_metadata_entity_details"
    assert entity["entity"]["fullyQualifiedName"] == (
        "Sample-ClickHouse.default.dm.ft_order_window_revenue"
    )
    assert entity["entity"]["columns"] == [
        {"name": "order_date", "dataType": "date"},
        {"name": "net_revenue", "dataType": "decimal"},
        {"name": "customer_id", "dataType": "string"},
    ]
    assert entity["columnNames"] == ["order_date", "net_revenue", "customer_id"]


def test_metadata_entity_details_keeps_complete_schema_columns_for_known_table() -> None:
    serializer = ToolResultPromptSerializer()
    column_names = [f"metric_{index}" for index in range(32)]
    tool_result = {
        "ok": True,
        "entity": {
            "entity": {
                "fullyQualifiedName": "Sample-ClickHouse.default.dm.orders_daily_report",
                "columns": [
                    {"name": column_name, "dataType": "Float64"}
                    for column_name in column_names
                ],
            },
            "columnNames": column_names,
        },
    }

    parsed = json.loads(
        serializer.serialize(tool_name="get_metadata_entity_details", tool_result=tool_result)
    )

    entity = parsed["compact_payload"]["entity"]
    assert [column["name"] for column in entity["entity"]["columns"]] == column_names
    assert entity["columnNames"] == column_names
    assert "columns_truncated" not in parsed["truncation_flags"]


def test_large_metadata_results_keep_structured_prompt_payload() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "count": 12,
        "tables": [
            {
                "source": "openmetadata",
                "fqn": f"Sample-ClickHouse.default.imp_prod.ECOM_SALES_REST_{index}",
                "description": "Daily ecommerce sales with revenue columns " * 20,
                "service": "Sample-ClickHouse",
                "database": "default",
                "schema": "imp_prod",
                "columns": [
                    {
                        "name": f"column_{column}",
                        "type": "String",
                        "description": "verbose column description " * 20,
                    }
                    for column in range(30)
                ],
                "relevance_score": 91.5,
                "matched_on": ["name", "columns"],
                "reason_codes": ["business_layer_preference"],
                "suspicious": False,
            }
            for index in range(12)
        ],
    }

    parsed = json.loads(
        serializer.serialize(tool_name="fetch_metadata_tables", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "fetch_metadata_tables"
    assert payload["count"] == 12
    assert payload["tables"]["items"][0]["fqn"] == (
        "Sample-ClickHouse.default.imp_prod.ECOM_SALES_REST_0"
    )
    assert payload["tables"]["items"][0]["columns"][0]["name"] == "column_0"
    assert "summary" not in payload
    assert "artifact_payload_truncated" in parsed["truncation_flags"]


def test_run_sql_truncated_preview_marks_preview_only_not_incomplete_execution() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "execution": {
            "execution_id": "exec-many",
            "status": "succeeded",
            "row_count": 112,
            "preview": {
                "columns": ["dt", "revenue"],
                "rows": [[f"2026-04-{day:02d}", day] for day in range(1, 113)],
            },
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="run_sql", tool_result=tool_result))

    run_sql = parsed["compact_payload"]["run_sql"]
    assert len(run_sql["rows"]) == 100
    assert run_sql["rows_truncated"] is True
    assert run_sql["user_hint"].startswith("Query succeeded")
    assert run_sql["prompt_row_limit"] == 100
    assert run_sql["max_prompt_row_limit"] == 500
    assert run_sql["prompt_rows_returned"] == 100
    assert run_sql["retrieval_contract"] == "bounded_prompt_preview"
    assert "answer-ready aggregate SQL" in run_sql["next_action_hint"]
    assert run_sql["execution_status"] == "succeeded"
    assert run_sql["row_count"] == 112


def test_run_sql_prompt_row_limit_can_be_requested_for_large_detail_window() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "prompt_row_limit": 250,
        "execution": {
            "execution_id": "exec-detail",
            "status": "succeeded",
            "row_count": 300,
            "applied_row_limit": 5000,
            "preview": {
                "columns": ["id"],
                "rows": [[index] for index in range(300)],
            },
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="run_sql", tool_result=tool_result))

    run_sql = parsed["compact_payload"]["run_sql"]
    assert len(run_sql["rows"]) == 250
    assert run_sql["prompt_row_limit"] == 250
    assert run_sql["max_prompt_row_limit"] == 500
    assert run_sql["prompt_rows_returned"] == 250
    assert run_sql["result_row_limit"] == 5000
    assert run_sql["rows_truncated"] is True


def test_prompt_artifact_truncation_flags_are_preserved() -> None:
    serializer = PromptArtifactSerializer()

    envelope = serializer.serialize(
        artifact_type="analysis_plan",
        artifact={
            "goal": "Explain revenue",
            "steps": [{"id": str(index), "title": "x" * 2_000} for index in range(10)],
        },
    )

    assert envelope.truncated is True
    assert envelope.truncation_flags == ["title_truncated", "steps_truncated"]


def test_prompt_artifact_output_is_stable() -> None:
    serializer = PromptArtifactSerializer()

    artifact = {
        "run_id": "run-1",
        "status": "running",
        "active_step_id": "segment_breakdown",
        "completed_step_ids": ["baseline"],
    }
    first = serializer.serialize(
        artifact_type="checkpoint_digest",
        artifact=artifact,
    )
    second = serializer.serialize(
        artifact_type="checkpoint_digest",
        artifact=artifact,
    )

    assert first.stable_json() == second.stable_json()
    assert json.loads(first.stable_json())["compact_payload"] == {
        "active_step_id": "segment_breakdown",
        "completed_step_ids": ["baseline"],
        "run_id": "run-1",
        "status": "running",
    }


def test_prompt_artifact_budget_warning_appears() -> None:
    class TinyTokenBudgetPolicy(PromptContextBudgetPolicy):
        @property
        def max_tokens(self) -> int:
            return 1

    serializer = PromptArtifactSerializer(budget_policy=TinyTokenBudgetPolicy())

    envelope = serializer.serialize(
        artifact_type="evidence_summary",
        artifact={
            "tool_name": "run_sql",
            "ok": True,
            "columns": ["column_name"],
        },
    )

    assert "prompt_artifact_budget_warning" in envelope.warnings
