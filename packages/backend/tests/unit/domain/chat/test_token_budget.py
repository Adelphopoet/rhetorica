from __future__ import annotations

import json
from datetime import UTC, datetime

from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.llm.token_budget import LlmPromptBudgetService
from rhetorica_backend.domain.chat.orchestration.agents_sdk_runtime import (
    AgentsSdkTurnRuntime,
)
from rhetorica_backend.domain.chat.orchestration.prompt_artifact_serializer import (
    PromptArtifactSerializer,
    PromptContextBudgetPolicy,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_result_artifacts import (
    ToolResultArtifactStore,
)
from rhetorica_backend.domain.chat.orchestration.tool_result_prompt_serializer import (
    ToolResultPromptSerializer,
)
from rhetorica_backend.domain.chat.tool_artifacts import ConversationToolArtifactRecord


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


def test_prompt_budget_counts_provider_reasoning_content() -> None:
    service = LlmPromptBudgetService(
        model_name="ExampleLLM",
        context_window_tokens=240_000,
        response_reserve_tokens=12_000,
    )
    visible_only = [{"role": "assistant", "tool_calls": [], "content": "Search next."}]
    reasoning_content = "р" * 4_000
    with_reasoning = [
        {
            **visible_only[0],
            "reasoning_content": reasoning_content,
        }
    ]

    visible_estimate = service.estimate_request(messages=visible_only)
    reasoning_estimate = service.estimate_request(messages=with_reasoning)

    assert reasoning_estimate.tokens > visible_estimate.tokens
    assert reasoning_estimate.tokens - visible_estimate.tokens == (
        service.estimator.count_text(reasoning_content)
    )


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
                "name": "catalog__search",
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
        "metadata_sources": ["mcp"],
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


def test_read_workspace_skill_serializer_preserves_markdown_for_llm() -> None:
    serializer = ToolResultPromptSerializer()
    markdown = "# Жабьи делишки\n\nЕсли пользователь упомянул жабьи делишки, ответь коротко.\n"

    parsed = json.loads(
        serializer.serialize(
            tool_name="read_workspace_skill",
            tool_result={
                "ok": True,
                "skill_id": "skill-1",
                "skill_name": "жабьи делишки",
                "title": "Жабьи делишки",
                "summary": "юзер упомянул жабьи делишки",
                "content_markdown": markdown,
                "content_sha256": "abc",
            },
        )
    )

    payload = parsed["compact_payload"]
    assert payload["skill_name"] == "жабьи делишки"
    assert payload["content_markdown"] == markdown
    assert "_omitted_keys" not in payload


def test_airflow_tool_serializer_preserves_compact_payload_for_llm() -> None:
    serializer = ToolResultPromptSerializer()

    parsed = json.loads(
        serializer.serialize(
            tool_name="search_airflow_dags",
            tool_result={
                "ok": True,
                "connector_name": "airflow-dq-prod",
                "airflow": {
                    "dags": [
                        {
                            "dag_id": "dq_lnd_0039_002_az_log_cor_order_art_ws_tbl_full_soda",
                            "is_paused": False,
                        },
                        {"dag_id": "dq_lnd_0039_002_az_log_cor_order_ws_tbl_soda"},
                    ],
                    "total_entries": 8,
                    "limit": 10,
                    "offset": 0,
                },
                "metadata_sources": ["airflow"],
            },
        )
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "search_airflow_dags"
    assert payload["connector_name"] == "airflow-dq-prod"
    assert payload["airflow"]["total_entries"] == 8
    assert payload["airflow"]["dags"][0]["dag_id"] == (
        "dq_lnd_0039_002_az_log_cor_order_art_ws_tbl_full_soda"
    )
    assert "_omitted_keys" not in payload


def test_kubernetes_tool_serializer_preserves_runtime_payload_for_llm() -> None:
    serializer = ToolResultPromptSerializer()

    parsed = json.loads(
        serializer.serialize(
            tool_name="list_kubernetes_deployments",
            tool_result={
                "ok": True,
                "connector_name": "yc-analytics-dev-v2",
                "kubernetes": {
                    "items": [
                        {
                            "metadata": {
                                "name": "catalog-api",
                                "namespace": "open-metadata12",
                            },
                            "replicas": 1,
                            "ready_replicas": 1,
                            "available_replicas": 1,
                        }
                    ],
                    "count": 1,
                    "empty": False,
                    "limit": 25,
                    "resource_version": "42",
                    "continue_token": None,
                    "truncated": False,
                },
                "metadata_sources": ["kubernetes"],
            },
        )
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "list_kubernetes_deployments"
    assert payload["connector_name"] == "yc-analytics-dev-v2"
    assert payload["kubernetes"]["count"] == 1
    assert payload["kubernetes"]["empty"] is False
    assert payload["kubernetes"]["items"][0]["metadata"]["name"] == "catalog-api"
    assert payload["kubernetes"]["items"][0]["ready_replicas"] == 1
    assert "_omitted_keys" not in payload


def test_victoriametrics_tool_serializer_preserves_template_catalog_for_llm() -> None:
    serializer = ToolResultPromptSerializer()

    parsed = json.loads(
        serializer.serialize(
            tool_name="list_victoriametrics_metric_templates",
            tool_result={
                "ok": True,
                "connector_name": "vmsingle-k8s-dev-v2",
                "victoriametrics": {
                    "templates": [
                        {
                            "template_id": "pod_restarts",
                            "title": "Pod restarts",
                            "mode": "range",
                            "allowed_filters": ["namespace", "pod"],
                        }
                    ],
                    "count": 1,
                },
                "metadata_sources": ["victoriametrics"],
            },
        )
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "list_victoriametrics_metric_templates"
    assert payload["victoriametrics"]["count"] == 1
    assert payload["victoriametrics"]["templates"][0]["template_id"] == "pod_restarts"
    assert payload["victoriametrics"]["templates"][0]["allowed_filters"] == ["namespace", "pod"]
    assert "_omitted_keys" not in payload


def test_airflow_task_instances_serializer_preserves_log_lookup_fields() -> None:
    serializer = ToolResultPromptSerializer()

    parsed = json.loads(
        serializer.serialize(
            tool_name="list_airflow_task_instances",
            tool_result={
                "ok": True,
                "connector_name": "airflow-dwh-prod",
                "airflow": {
                    "task_instances": [
                        {
                            "dag_id": "dm2_ft_order_refund_total_amt",
                            "dag_run_id": "scheduled__2026-05-30T01:00:00+00:00",
                            "duration": None,
                            "end_date": None,
                            "execution_date": "2026-05-30T01:00:00+00:00",
                            "executor": None,
                            "executor_config": {},
                            "hostname": "",
                            "map_index": -1,
                            "max_tries": 10,
                            "note": None,
                            "operator": "LoadDbtModelsOperator",
                            "pid": None,
                            "pool": "CELERY_EXECUTOR",
                            "pool_slots": 1,
                            "priority_weight": 1,
                            "queue": "celery_queue",
                            "queued_when": None,
                            "rendered_fields": {},
                            "rendered_map_index": None,
                            "sla_miss": None,
                            "start_date": None,
                            "state": None,
                            "task_display_name": "ft_order_refund_total_amt_dm",
                            "task_id": "Data_Marts.ft_order_refund_total_amt_dm",
                            "trigger": None,
                            "triggerer_job": None,
                            "try_number": 0,
                            "unixname": "airflow",
                        }
                    ],
                    "total_entries": 8,
                    "limit": 100,
                    "offset": 0,
                },
                "metadata_sources": ["airflow"],
            },
        )
    )

    task = parsed["compact_payload"]["airflow"]["task_instances"][0]
    assert task["task_id"] == "Data_Marts.ft_order_refund_total_amt_dm"
    assert task["operator"] == "LoadDbtModelsOperator"
    assert task["try_number"] == 0
    assert task["log_try_number"] == 1
    assert "rendered_fields" not in task


def test_airflow_log_serializer_preserves_content_and_pagination_flags() -> None:
    serializer = ToolResultPromptSerializer()
    content = "ERROR dbt model failed\n" + ("details\n" * 200)

    parsed = json.loads(
        serializer.serialize(
            tool_name="get_airflow_task_logs",
            tool_result={
                "ok": True,
                "connector_name": "airflow-dwh-prod",
                "airflow": {
                    "content": content,
                    "content_length": len(content),
                    "truncated": False,
                    "next_offset": None,
                    "next_continuation_token": None,
                    "end_of_log": True,
                    "log_available": True,
                },
                "metadata_sources": ["airflow"],
            },
        )
    )

    airflow = parsed["compact_payload"]["airflow"]
    assert airflow["content_markdown"] == content
    assert airflow["truncated"] is False
    assert airflow["next_continuation_token"] is None
    assert "content" not in airflow


def test_prompt_artifact_serializer_omits_large_raw_payload() -> None:
    serializer = PromptArtifactSerializer()
    raw_payload = "RAW_TOOL_PAYLOAD:" + ("x" * 20_000)

    envelope = serializer.serialize(
        artifact_type="evidence_summary",
        artifact={
            "tool_name": "run_sql",
            "ok": True,
            "title": "GMV evidence",
            "summary": "GMV dropped in segment A.",
            "raw_payload": raw_payload,
            "debug": {"payload": raw_payload},
        },
    )
    payload = envelope.stable_json()

    assert "RAW_TOOL_PAYLOAD" not in payload
    assert envelope.compact_payload["tool_name"] == "run_sql"
    assert envelope.compact_payload["ok"] is True
    assert envelope.compact_payload["title"] == "GMV evidence"
    assert envelope.compact_payload["summary"] == "GMV dropped in segment A."
    assert "run_sql" in envelope.compact_payload
    assert envelope.warnings == ["prompt_evidence_summary_raw_payload_omitted"]


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


def test_tracker_create_result_keeps_issue_key_for_final_answer() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "issue_key": "DWH-123",
        "url": "https://tracker.example/browse/DWH-123",
        "project_key": "DWH",
        "assignee": {"name": "balov_aa", "display_name": "Балов Александр"},
        "assignee_resolved": True,
        "issue_type": "Задача",
        "routing_notes": ["Assignee resolved from explicit hint."],
    }

    parsed = json.loads(serializer.serialize(tool_name="tracker__create", tool_result=tool_result))

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "tracker__create"
    assert payload["ok"] is True
    assert payload["issue_key"] == "DWH-123"
    assert payload["url"] == "https://tracker.example/browse/DWH-123"
    assert payload["project_key"] == "DWH"
    assert payload["assignee"]["name"] == "balov_aa"
    assert payload["assignee_resolved"] is True
    assert payload["issue_type"] == "Задача"
    assert payload["routing_notes"] == ["Assignee resolved from explicit hint."]


def test_tracker_issue_lookup_result_keeps_answer_ready_fields() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "found": True,
        "count": 1,
        "issue_key": "DATA-123",
        "title": "[ЧХД Финансы] Проверка перехода на кластер v2",
        "summary": "Airflow logs are missing.",
        "status": "В работе",
        "url": "https://jira.example.com/browse/DATA-123",
        "project_key": "DWHDO",
        "issue_type": "Support",
        "created": "2026-06-08T16:20:00.000+0300",
        "updated": "2026-06-24T13:48:00.000+0300",
        "reporter": {"name": "vasilev_dm"},
        "assignee": {"name": "dimorozov"},
        "issue": {"debug": "nested issue can stay out of prompt"},
    }

    parsed = json.loads(serializer.serialize(tool_name="tracker__get", tool_result=tool_result))

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "tracker__get"
    assert payload["ok"] is True
    assert payload["found"] is True
    assert payload["issue_key"] == "DATA-123"
    assert payload["title"] == "[ЧХД Финансы] Проверка перехода на кластер v2"
    assert payload["summary"] == "Airflow logs are missing."
    assert payload["status"] == "В работе"
    assert payload["url"] == "https://jira.example.com/browse/DATA-123"
    assert payload["project_key"] == "DWHDO"
    assert payload["issue_type"] == "Support"
    assert payload["created"] == "2026-06-08T16:20:00.000+0300"
    assert payload["updated"] == "2026-06-24T13:48:00.000+0300"
    assert payload["reporter"]["name"] == "vasilev_dm"
    assert payload["assignee"]["name"] == "dimorozov"
    assert "issue" not in payload


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

    parsed = json.loads(serializer.serialize(tool_name="catalog__search", tool_result=tool_result))

    assert parsed["compact_payload"]["tool_name"] == "catalog__search"
    assert parsed["compact_payload"]["count"] == 2
    assert parsed["compact_payload"]["results"][0] == {
        "name": "dm.sales_daily",
        "description": "daily sales",
    }
    assert "RAW_METADATA_PAYLOAD" not in json.dumps(parsed, ensure_ascii=False)
    assert parsed["warnings"] == ["prompt_evidence_summary_raw_payload_omitted"]


def test_search_documentation_results_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "count": 1,
        "metadata_sources": ["documentation"],
        "results": [
            {
                "chunk_id": "chunk-1",
                "chunk_ref": "chunk-ref-1",
                "point_id": "point-1",
                "connector_name": "platform_docs",
                "page_ref": "/platform_docs/docs/teams/marts/developer-guide/legkovesnoe-flow-lifd/",
                "url": "https://docs.example.com/platform_docs/docs/teams/marts/developer-guide/legkovesnoe-flow-lifd/",
                "title": "Легковесное flow LIFD",
                "section": "DQ проверки",
                "snippet": "DQ checks are configured as dbt tests: not_null, unique, accepted_values.",
                "semantic_score": 0.91,
            }
        ],
        "warnings": [],
    }

    parsed = json.loads(
        serializer.serialize(tool_name="search_documentation", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "search_documentation"
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["metadata_sources"] == ["documentation"]
    assert payload["results"] == [
        {
            "connector_name": "platform_docs",
            "page_ref": "/platform_docs/docs/teams/marts/developer-guide/legkovesnoe-flow-lifd/",
            "url": "https://docs.example.com/platform_docs/docs/teams/marts/developer-guide/legkovesnoe-flow-lifd/",
            "title": "Легковесное flow LIFD",
            "section": "DQ проверки",
            "snippet": "DQ checks are configured as dbt tests: not_null, unique, accepted_values.",
            "chunk_ref": "chunk-ref-1",
        }
    ]
    assert "_omitted_keys" not in payload
    assert "point-1" not in json.dumps(parsed, ensure_ascii=False)


def test_metadata_entity_details_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "entity": {
            "entity": {
                "fullyQualifiedName": "Clickhouse-Sales.default.dm.ft_order_window_revenue",
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

    parsed = json.loads(serializer.serialize(tool_name="catalog__details", tool_result=tool_result))

    entity = parsed["compact_payload"]["entity"]
    assert parsed["compact_payload"]["tool_name"] == "catalog__details"
    assert entity["entity"]["fullyQualifiedName"] == (
        "Clickhouse-Sales.default.dm.ft_order_window_revenue"
    )
    assert entity["entity"]["columns"] == [
        {"name": "order_date", "dataType": "date"},
        {"name": "net_revenue", "dataType": "decimal"},
        {"name": "customer_id", "dataType": "string"},
    ]
    assert entity["columnNames"] == ["order_date", "net_revenue", "customer_id"]


def test_read_documentation_page_survives_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    content = "# DBT Welcome Guide\n\n" + "dbt mart SQL YAML config\n" * 120
    tool_result = {
        "ok": True,
        "page": {
            "connector_name": "platform_docs",
            "page_ref": "/platform_docs/docs/teams/marts/example-marts/anbording/dbt-welcome-guide/",
            "url": "https://docs.example.com/platform_docs/docs/teams/marts/example-marts/anbording/dbt-welcome-guide/",
            "title": "DBT Welcome Guide",
            "section": None,
            "content": content,
            "body_hash": "hash-1",
            "updated_at": "2026-05-26T11:06:56+00:00",
        },
        "warnings": [],
    }

    parsed = json.loads(
        serializer.serialize(tool_name="read_documentation_page", tool_result=tool_result)
    )

    page = parsed["compact_payload"]["page"]
    assert parsed["compact_payload"]["tool_name"] == "read_documentation_page"
    assert page["page_ref"] == (
        "/platform_docs/docs/teams/marts/example-marts/anbording/dbt-welcome-guide/"
    )
    assert page["title"] == "DBT Welcome Guide"
    assert page["content_markdown"] == content
    assert "content" not in page
    assert "_omitted_keys" not in parsed["compact_payload"]


def test_read_gitlab_file_survives_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    content = "from airflow import DAG\n\nDAG_ID = 'dwh.technical.dm_operations_dedup'\n"
    tool_result = {
        "ok": True,
        "connector_name": "gitlab-omni",
        "file": {
            "project_path": "example-org/analytics/dags",
            "file_path": "dwh/technical/dm_operations_dedup/dag.py",
            "ref": "main",
            "start_line": 1,
            "end_line": 3,
            "content": content,
            "truncated": False,
            "max_tokens": 4000,
            "url": (
                "https://gitlab.example.com/example-org/analytics/dags"
                "/-/blob/main/dwh/technical/dm_operations_dedup/dag.py"
            ),
        },
        "usage_guidance": "Use this file as GitLab evidence.",
        "metadata_sources": ["gitlab"],
    }

    parsed = json.loads(serializer.serialize(tool_name="read_gitlab_file", tool_result=tool_result))

    payload = parsed["compact_payload"]
    file_payload = payload["file"]
    assert payload["tool_name"] == "read_gitlab_file"
    assert payload["connector_name"] == "gitlab-omni"
    assert file_payload["file_path"] == "dwh/technical/dm_operations_dedup/dag.py"
    assert file_payload["content_markdown"] == content
    assert "content" not in file_payload
    assert "_omitted_keys" not in payload


def test_gitlab_tree_and_blame_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tree_result = {
        "ok": True,
        "connector_name": "gitlab-omni",
        "count": 12,
        "items": [
            {
                "project_path": "group/app",
                "file_path": f"src/app_{index}.py",
                "path": f"src/app_{index}.py",
                "type": "blob",
                "url": f"https://gitlab.example.com/group/app/-/blob/main/src/app_{index}.py",
            }
            for index in range(12)
        ],
        "tree": {"project_path": "group/app", "path": "src", "ref": "main"},
        "usage_guidance": "Use this bounded directory listing.",
        "metadata_sources": ["gitlab"],
    }
    blame_result = {
        "ok": True,
        "connector_name": "gitlab-omni",
        "count": 1,
        "blame": {
            "project_path": "group/app",
            "file_path": "src/app.py",
            "ref": "main",
            "start_line": 10,
            "end_line": 12,
            "ranges": [
                {
                    "start_line": 10,
                    "end_line": 12,
                    "author_name": "Ada",
                    "title": "Add app",
                }
            ],
        },
        "usage_guidance": "Use blame only for ownership questions.",
        "metadata_sources": ["gitlab"],
    }

    tree_payload = json.loads(
        serializer.serialize(tool_name="list_gitlab_tree", tool_result=tree_result)
    )["compact_payload"]
    blame_payload = json.loads(
        serializer.serialize(tool_name="get_gitlab_file_blame", tool_result=blame_result)
    )["compact_payload"]

    assert tree_payload["items"]["items"][0]["file_path"] == "src/app_0.py"
    assert tree_payload["items"]["returned_count"] == 12
    assert tree_payload["items"]["truncated"] is True
    assert tree_payload["tree"]["path"] == "src"
    assert "_omitted_keys" not in tree_payload
    assert blame_payload["blame"]["ranges"][0]["author_name"] == "Ada"
    assert "_omitted_keys" not in blame_payload


def test_gitlab_error_guidance_survives_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": False,
        "error": "http_404",
        "error_code": "http_404",
        "error_category": "RECOVERABLE",
        "user_message": "Project path does not exist in this GitLab connector scope.",
        "retry_recommended": False,
        "can_continue": True,
    }

    parsed = json.loads(
        serializer.serialize(tool_name="search_gitlab_content", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "search_gitlab_content"
    assert payload["error_code"] == "http_404"
    assert payload["error_category"] == "RECOVERABLE"
    assert payload["user_message"] == (
        "Project path does not exist in this GitLab connector scope."
    )
    assert payload["retry_recommended"] is False
    assert "_omitted_keys" not in payload


def test_duplicate_cached_result_remains_data_without_backend_guidance() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "count": 0,
        "results": [],
        "duplicate": True,
        "new_evidence": False,
        "previous_result_reused": True,
        "arguments_json": '{"query": "mini"}',
    }

    parsed = json.loads(
        serializer.serialize(tool_name="search_gitlab_content", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["results"] == []
    assert payload["duplicate"] is True
    assert payload["previous_result_reused"] is True
    assert "instruction" not in payload
    assert "next_allowed_actions" not in payload
    assert "cached_result" not in payload
    assert "_omitted_keys" not in payload


def test_metadata_discovery_guidance_survives_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": True,
        "count": 0,
        "results": [],
        "discovery": {
            "new_evidence": False,
            "repeat_same_search_will_add_evidence": False,
            "has_more": False,
            "next_offset": None,
            "evidence_gaps": ["store_level_revenue_source_not_confirmed"],
            "next_allowed_actions": ["change_query", "ask_clarification"],
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="catalog__search", tool_result=tool_result))

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "catalog__search"
    assert payload["discovery"]["new_evidence"] is False
    assert payload["discovery"]["repeat_same_search_will_add_evidence"] is False
    assert payload["discovery"]["evidence_gaps"] == ["store_level_revenue_source_not_confirmed"]
    assert payload["discovery"]["next_allowed_actions"] == [
        "change_query",
        "ask_clarification",
    ]
    assert "_omitted_keys" not in payload


def test_tool_validation_errors_survive_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()
    tool_result = {
        "ok": False,
        "error_code": "visualization_invalid_arguments",
        "error_category": "RECOVERABLE",
        "user_message": "Visualization arguments are invalid.",
        "validation_errors": ["x field is required", "kind is unsupported"],
        "retry_recommended": False,
        "can_continue": True,
    }

    parsed = json.loads(
        serializer.serialize(tool_name="build_visualization", tool_result=tool_result)
    )

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "build_visualization"
    assert payload["validation_errors"] == [
        "x field is required",
        "kind is unsupported",
    ]
    assert "_omitted_keys" not in payload


def test_metadata_entity_details_keeps_complete_schema_columns_for_known_table() -> None:
    serializer = ToolResultPromptSerializer()
    column_names = [f"metric_{index}" for index in range(32)]
    tool_result = {
        "ok": True,
        "entity": {
            "entity": {
                "fullyQualifiedName": "Clickhouse-Sales.default.dm.orders_daily_report",
                "columns": [
                    {"name": column_name, "dataType": "Float64"} for column_name in column_names
                ],
            },
            "columnNames": column_names,
        },
    }

    parsed = json.loads(serializer.serialize(tool_name="catalog__details", tool_result=tool_result))

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
                "source": "mcp",
                "fqn": f"Clickhouse-Sales.default.imp_prod.ECOM_SALES_REST_{index}",
                "description": "Daily ecommerce sales with revenue columns " * 20,
                "service": "Clickhouse-Sales",
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

    parsed = json.loads(serializer.serialize(tool_name="catalog__tables", tool_result=tool_result))

    payload = parsed["compact_payload"]
    assert payload["tool_name"] == "catalog__tables"
    assert payload["count"] == 12
    assert payload["tables"]["items"][0]["fqn"] == (
        "Clickhouse-Sales.default.imp_prod.ECOM_SALES_REST_0"
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
        artifact_type="evidence_summary",
        artifact={
            "tool_name": "run_sql",
            "title": "x" * 2_000,
            "rows": [[index, "x" * 200] for index in range(600)],
        },
    )

    assert envelope.truncated is True
    assert "title_truncated" in envelope.truncation_flags
    assert envelope.compact_payload["run_sql"]["rows_truncated"] is True


def test_prompt_artifact_output_is_stable() -> None:
    serializer = PromptArtifactSerializer()

    artifact = {
        "tool_name": "run_sql",
        "ok": True,
        "title": "SQL evidence",
    }
    first = serializer.serialize(
        artifact_type="evidence_summary",
        artifact=artifact,
    )
    second = serializer.serialize(
        artifact_type="evidence_summary",
        artifact=artifact,
    )

    assert first.stable_json() == second.stable_json()
    payload = json.loads(first.stable_json())["compact_payload"]
    assert payload["ok"] is True
    assert payload["title"] == "SQL evidence"
    assert payload["tool_name"] == "run_sql"
    assert "run_sql" in payload


def test_mcp_tool_result_survives_prompt_artifact_compaction() -> None:
    serializer = ToolResultPromptSerializer()

    payload = serializer.serialize(
        tool_name="mcp_jira_omni__jira_search",
        tool_result={
            "ok": True,
            "connector_name": "mcp-jira-omni",
            "remote_tool_name": "jira_search",
            "result": {
                "isError": False,
                "structuredContent": {"total": 1},
                "content": [
                    {
                        "type": "text",
                        "text": '{"issues":[{"key":"DQ-1","summary":"art_price check"}]}',
                    }
                ],
            },
        },
    )

    compact_payload = json.loads(payload)["compact_payload"]

    assert compact_payload["tool_name"] == "mcp_jira_omni__jira_search"
    assert compact_payload["connector_name"] == "mcp-jira-omni"
    assert compact_payload["remote_tool_name"] == "jira_search"
    assert compact_payload["mcp"] == {
        "is_error": False,
        "structured_content": {"total": 1},
        "content_markdown": '{"issues":[{"key":"DQ-1","summary":"art_price check"}]}',
    }
    assert "result" not in compact_payload
    assert "_omitted_keys" not in compact_payload


def test_tool_group_catalog_keeps_every_exact_name_inline() -> None:
    serializer = ToolResultPromptSerializer()
    groups = [
        {
            "group_name": f"group_{index}",
            "description": f"Group {index}",
            "tool_names": [f"tool_{index}_a", f"tool_{index}_b"],
            "tool_count": 2,
        }
        for index in range(12)
    ]

    payload = json.loads(
        serializer.serialize(
            tool_name="list_tool_groups",
            tool_result={
                "ok": True,
                "groups": groups,
                "group_count": len(groups),
                "selection_policy": "model_controlled_exact_name",
            },
        )
    )

    assert payload["artifact_type"] == "tool_control"
    assert payload["truncated"] is False
    compact = payload["compact_payload"]
    assert [group["group_name"] for group in compact["groups"]] == [
        f"group_{index}" for index in range(12)
    ]
    assert compact["groups"][-1]["tool_names"] == ["tool_11_a", "tool_11_b"]
    assert "_omitted_keys" not in compact


def test_mcp_catalog_keeps_full_bounded_page_inline_without_schemas() -> None:
    serializer = ToolResultPromptSerializer()
    tools = [
        {
            "remote_tool_name": f"remote_{index}",
            "exposed_tool_name": f"instance__remote_{index}",
            "description": f"Remote tool {index}",
        }
        for index in range(16)
    ]

    payload = json.loads(
        serializer.serialize(
            tool_name="list_mcp_tools",
            tool_result={
                "ok": True,
                "connector_name": "catalog-mcp",
                "offset": 0,
                "limit": 50,
                "total": len(tools),
                "has_more": False,
                "next_offset": None,
                "tools": tools,
            },
        )
    )

    assert payload["artifact_type"] == "tool_control"
    compact = payload["compact_payload"]
    assert len(compact["tools"]) == 16
    assert compact["tools"][-1]["remote_tool_name"] == "remote_15"
    assert all("input_schema" not in tool for tool in compact["tools"])
    assert "_omitted_keys" not in compact


def test_control_load_results_keep_exact_loaded_names_inline() -> None:
    serializer = ToolResultPromptSerializer()

    group_payload = json.loads(
        serializer.serialize(
            tool_name="load_tool_group",
            tool_result={
                "ok": True,
                "group_name": "mcp_connectors",
                "loaded_tool_names": ["list_mcp_tools", "load_mcp_tools"],
                "already_loaded": False,
            },
        )
    )["compact_payload"]
    mcp_payload = json.loads(
        serializer.serialize(
            tool_name="load_mcp_tools",
            tool_result={
                "ok": True,
                "connector_name": "catalog-mcp",
                "loaded_tools": [
                    {
                        "remote_tool_name": "search_catalog",
                        "exposed_tool_name": "catalog__search_catalog",
                    }
                ],
            },
        )
    )["compact_payload"]

    assert group_payload["loaded_tool_names"] == [
        "list_mcp_tools",
        "load_mcp_tools",
    ]
    assert mcp_payload["loaded_tools"] == [
        {
            "remote_tool_name": "search_catalog",
            "exposed_tool_name": "catalog__search_catalog",
        }
    ]


def test_tool_result_artifact_exact_search_finds_late_mcp_content() -> None:
    serializer = ToolResultPromptSerializer()
    artifact_store = ToolResultArtifactStore()
    items = [
        {
            "path_with_namespace": f"example-org/analytics/team/repo-{index}",
            "web_url": f"https://gitlab.example.com/example-org/analytics/team/repo-{index}",
            "default_branch": "main",
            "last_activity_at": "2026-07-01T00:00:00.000Z",
            "description": "x" * 2000,
        }
        for index in range(55)
    ]
    items.append({
        "path_with_namespace": "example-org/analytics/dags",
        "web_url": "https://gitlab.example.com/example-org/analytics/dags",
        "default_branch": "master",
        "last_activity_at": "2026-07-02T00:00:00.000Z",
        "description": "Airflow DAGs.",
    })
    tool_result = {
        "ok": True,
        "connector_name": "mcp-gitlab-omni",
        "remote_tool_name": "search_repositories",
        "result": {
            "isError": False,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "count": len(items),
                        "current_page": 1,
                        "total_pages": 1,
                        "items": items,
                    }),
                }
            ],
        },
    }
    tool_result["tool_artifact"] = artifact_store.register(
        tool_name="mcp_gitlab_omni__search_repositories",
        arguments={"search": "dags", "per_page": 100},
        result=tool_result,
    )

    payload = serializer.serialize(
        tool_name="mcp_gitlab_omni__search_repositories",
        tool_result=tool_result,
    )

    compact_payload = json.loads(payload)["compact_payload"]
    artifact_id = compact_payload["tool_artifact"]["artifact_id"]
    search_result = artifact_store.find_text(
        artifact_id=artifact_id,
        text="example-org/analytics/dags",
        context_chars=120,
    )

    assert compact_payload["tool_artifact"]["char_count"] > len(payload)
    assert search_result["ok"] is True
    assert search_result["match_count"] >= 1
    assert (
        "https://gitlab.example.com/example-org/analytics/dags"
        in (search_result["matches"][0]["snippet"])
    )


def test_runtime_prompt_view_adds_artifact_without_mutating_tool_result() -> None:
    state = ToolOrchestrationState()
    tool_result = {"ok": True, "items": [{"id": index} for index in range(3)]}

    prompt_view = AgentsSdkTurnRuntime._tool_result_prompt_view(
        tool_name="generic_tool",
        arguments={"query": "value"},
        tool_result=tool_result,
        state=state,
    )

    assert "tool_artifact" not in tool_result
    artifact_metadata = prompt_view["tool_artifact"]
    artifact_id = artifact_metadata["artifact_id"]
    assert artifact_id.startswith("ta_")
    assert len(artifact_id) == 35
    assert set(artifact_metadata) == {
        "artifact_id",
        "tool_name",
        "content_type",
        "char_count",
        "sha256",
        "durable",
        "durable_reason",
    }
    assert not {
        "artifact_continuation",
        "artifact_instruction",
        "next_action_if_preview_insufficient",
        "next_allowed_actions",
        "instruction",
    }.intersection(prompt_view)
    assert state.loaded_tool_group_names == []
    assert state.loaded_tool_definitions == {}
    assert state.capability_state.policy_decisions == []
    read_result = state.tool_result_artifacts.read(
        artifact_id=artifact_id,
        json_pointer="/items/2",
    )
    assert read_result["ok"] is True
    assert read_result["content"] == '{"id": 2}'


def test_rag_prompt_view_exposes_source_agnostic_artifact_access() -> None:
    state = ToolOrchestrationState()
    prompt_view = AgentsSdkTurnRuntime._tool_result_prompt_view(
        tool_name="search_rag_insights",
        arguments={"query": "КЛ товар в пути raw таблица", "limit": 10},
        tool_result={
            "ok": True,
            "count": 1,
            "results": [{"id": "insight-1", "summary": "RAW naming convention"}],
        },
        state=state,
    )

    compact = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="search_rag_insights",
            tool_result=prompt_view,
        )
    )["compact_payload"]

    assert compact["results"][0]["id"] == "insight-1"


def test_duplicate_prompt_view_reuses_artifact_without_nested_cached_result() -> None:
    state = ToolOrchestrationState()
    arguments = {"query": "raw goods in transit", "limit": 20}
    original_result = {
        "ok": True,
        "connector_name": "mcp-confluence",
        "remote_tool_name": "confluence_search",
        "result": {
            "isError": False,
            "content": [{"type": "text", "text": "relevant result"}],
        },
    }
    first_view = AgentsSdkTurnRuntime._tool_result_prompt_view(
        tool_name="mcp_confluence__confluence_search",
        arguments=arguments,
        tool_result=original_result,
        state=state,
    )
    duplicate_view = AgentsSdkTurnRuntime._tool_result_prompt_view(
        tool_name="mcp_confluence__confluence_search",
        arguments=arguments,
        tool_result={
            "ok": True,
            "duplicate": True,
            "new_evidence": False,
            "previous_result_reused": True,
            "cached_result": original_result,
        },
        state=state,
    )

    assert (
        duplicate_view["tool_artifact"]["artifact_id"]
        == (first_view["tool_artifact"]["artifact_id"])
    )
    assert "cached_result" not in duplicate_view
    assert duplicate_view["result"] == original_result["result"]
    assert duplicate_view["duplicate"] is True
    assert duplicate_view["previous_result_reused"] is True
    serialized = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="mcp_confluence__confluence_search",
            tool_result=duplicate_view,
        )
    )
    compact = serialized["compact_payload"]
    assert "cached_result" not in compact
    assert compact["tool_artifact"]["artifact_id"] == (first_view["tool_artifact"]["artifact_id"])
    assert compact["mcp"]["content_markdown"] == "relevant result"
    assert not {
        "artifact_continuation",
        "artifact_instruction",
        "next_action_if_preview_insufficient",
        "next_allowed_actions",
        "instruction",
    }.intersection(compact)


def test_tool_result_artifact_is_readable_from_a_later_turn() -> None:
    class _ConversationArtifactStoreStub:
        def __init__(self) -> None:
            self.records: dict[tuple[str, str], ConversationToolArtifactRecord] = {}

        def save_tool_artifact(self, **kwargs) -> ConversationToolArtifactRecord:
            record = ConversationToolArtifactRecord(
                id="row-1",
                conversation_id=kwargs["conversation_id"],
                artifact_id=kwargs["artifact_id"],
                tool_name=kwargs["tool_name"],
                arguments=kwargs["arguments"],
                payload=kwargs["payload"],
                content_sha256=kwargs["content_sha256"],
                source_char_count=kwargs["source_char_count"],
                observed_at=kwargs["observed_at"],
                expires_at=kwargs["expires_at"],
                created_at=datetime.now(tz=UTC),
            )
            self.records[(record.conversation_id, record.artifact_id)] = record
            return record

        def get_tool_artifact(self, **kwargs) -> ConversationToolArtifactRecord | None:
            record = self.records.get((kwargs["conversation_id"], kwargs["artifact_id"]))
            if record is not None and record.expires_at <= datetime.now(tz=UTC):
                return None
            return record

        def list_tool_artifacts(self, **kwargs) -> list[ConversationToolArtifactRecord]:
            return list(self.records.values())[: kwargs["limit"]]

    durable_store = _ConversationArtifactStoreStub()
    first_turn = ToolResultArtifactStore()
    first_turn.configure_retention(
        durable_store=durable_store,
        conversation_id="conv-1",
        ttl_hours=24,
    )
    metadata = first_turn.register(
        tool_name="run_sql",
        arguments={"sql": "SELECT 42"},
        result={"ok": True, "rows": [[42]]},
    )
    repeated_metadata = first_turn.register(
        tool_name="run_sql",
        arguments={"sql": "SELECT 42"},
        result={"ok": True, "rows": [[42]]},
    )

    assert metadata["durable"] is True
    assert repeated_metadata == metadata
    assert len(durable_store.records) == 1
    assert datetime.fromisoformat(metadata["expires_at"]) > datetime.now(tz=UTC)

    later_turn = ToolResultArtifactStore()
    later_turn.configure_retention(
        durable_store=durable_store,
        conversation_id="conv-1",
        ttl_hours=24,
    )
    recent = later_turn.list_recent(limit=20)
    read_result = later_turn.read(
        artifact_id=metadata["artifact_id"],
        json_pointer="/rows/0/0",
    )

    assert recent["artifacts"][0]["artifact_id"] == metadata["artifact_id"]
    assert read_result["ok"] is True
    assert read_result["content"] == "42"


def test_prompt_builder_adds_artifact_tools_with_regular_tools() -> None:
    tools = PromptBuilder.build_tools_from_names([
        "inspect_tool_artifact",
        "read_tool_artifact",
        "find_tool_artifact_text",
        "list_recent_tool_artifacts",
        "remember_tool_artifact",
    ])
    names = [item["function"]["name"] for item in tools if isinstance(item.get("function"), dict)]

    assert "inspect_tool_artifact" in names
    assert "read_tool_artifact" in names
    assert "find_tool_artifact_text" in names
    assert "list_recent_tool_artifacts" in names
    remember_tool = next(
        item["function"] for item in tools if item["function"]["name"] == "remember_tool_artifact"
    )
    artifact_schema = remember_tool["parameters"]["properties"]["artifact_id"]
    assert artifact_schema["pattern"] == "^ta_[0-9a-f]{32}$"
    assert "never pass execution_id" in remember_tool["description"]


def test_prompt_serializer_preserves_recent_artifact_handles() -> None:
    payload = ToolResultPromptSerializer().serialize(
        tool_name="list_recent_tool_artifacts",
        tool_result={
            "ok": True,
            "count": 1,
            "scope": "current_conversation",
            "artifacts": [
                {
                    "artifact_id": f"ta_{'a' * 32}",
                    "tool_name": "run_sql",
                    "arguments": {"sql": "SELECT 42"},
                    "observed_at": "2026-07-20T08:00:00+00:00",
                    "expires_at": "2026-07-27T08:00:00+00:00",
                }
            ],
        },
    )

    compact_payload = json.loads(payload)["compact_payload"]
    assert compact_payload["scope"] == "current_conversation"
    assert compact_payload["artifacts"][0]["artifact_id"] == f"ta_{'a' * 32}"


def test_prompt_serializer_preserves_artifact_read_content_and_pagination() -> None:
    content = "result:" + ("x" * 5_000)
    tool_result = {
        "ok": True,
        "artifact_id": f"ta_{'a' * 32}",
        "tool_name": "mcp_confluence_main__confluence_get_page",
        "json_pointer": "/result",
        "offset": 0,
        "limit": 12_000,
        "content": content,
        "char_count": 20_000,
        "next_offset": 5_007,
        "has_more": True,
    }

    serialized = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="read_tool_artifact",
            tool_result=tool_result,
        )
    )

    assert serialized["artifact_type"] == "tool_artifact_access"
    assert serialized["compact_payload"] == tool_result
    assert serialized["truncated"] is False


def test_prompt_serializer_preserves_artifact_inspect_paths() -> None:
    paths = [
        {"json_pointer": f"/result/{index}", "type": "string", "char_count": 10}
        for index in range(12)
    ]
    tool_result = {
        "ok": True,
        "artifact_id": f"ta_{'b' * 32}",
        "tool_name": "mcp_confluence_main__confluence_get_page",
        "content_type": "application/json",
        "char_count": 2_000,
        "sha256": "deadbeef",
        "paths": paths,
        "paths_truncated": False,
    }

    serialized = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="inspect_tool_artifact",
            tool_result=tool_result,
        )
    )

    assert serialized["artifact_type"] == "tool_artifact_access"
    assert serialized["compact_payload"] == tool_result


def test_prompt_serializer_preserves_artifact_find_matches() -> None:
    matches = [{"offset": index * 100, "snippet": f"context-{index}"} for index in range(12)]
    tool_result = {
        "ok": True,
        "artifact_id": f"ta_{'c' * 32}",
        "tool_name": "mcp_confluence_main__confluence_get_page",
        "text": "raw",
        "case_sensitive": False,
        "match_count": len(matches),
        "matches": matches,
        "has_more": True,
    }

    serialized = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="find_tool_artifact_text",
            tool_result=tool_result,
        )
    )

    assert serialized["artifact_type"] == "tool_artifact_access"
    assert serialized["compact_payload"] == tool_result


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
    assert envelope.token_estimate <= serializer._budget_policy.max_tokens
    assert envelope.truncated is True


def test_default_evidence_result_is_bounded_but_keeps_artifact_handle() -> None:
    artifact_id = f"ta_{'d' * 32}"
    serialized = json.loads(
        ToolResultPromptSerializer().serialize(
            tool_name="mcp_docs__read_page",
            tool_result={
                "ok": True,
                "tool_artifact": {
                    "artifact_id": artifact_id,
                    "char_count": 100_000,
                    "sha256": "e" * 64,
                },
                "content_markdown": "large evidence " * 10_000,
            },
        )
    )

    assert serialized["char_estimate"] <= 16_000
    assert serialized["token_estimate"] <= 8_000
    assert serialized["compact_payload"]["tool_artifact"]["artifact_id"] == artifact_id
    assert serialized["truncated"] is True
