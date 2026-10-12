from __future__ import annotations

from rhetorica_backend.contracts.chat import SemanticGrounding, SemanticGroundingCandidate
from rhetorica_backend.contracts.execution import ExecuteSqlResponse, QueryPreview
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)


def test_assistant_message_for_history_strips_thinking_blocks() -> None:
    message = {
        "content": "<think>Let me search forever.</think>\n\n",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_gitlab_content", "arguments": "{}"},
            }
        ],
    }

    history_message = shared_kernel.assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "tool_calls": message["tool_calls"],
    }


def test_assistant_message_for_history_preserves_provider_reasoning_for_tool_call() -> None:
    message = {
        "content": None,
        "reasoning_content": "  inspect the empty result before continuing  ",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    }

    history_message = shared_kernel.assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "reasoning_content": message["reasoning_content"],
        "tool_calls": message["tool_calls"],
    }


def test_assistant_message_for_history_canonicalizes_malformed_tool_arguments() -> None:
    message = {
        "content": None,
        "reasoning_content": "Call the tool, then inspect its error.",
        "tool_calls": [
            {
                "id": "call-broken",
                "type": "function",
                "function": {
                    "name": "catalog__search",
                    "arguments": '{"query":"unfinished',
                },
            }
        ],
    }

    history_message = shared_kernel.assistant_message_for_history(message)

    assert history_message["reasoning_content"] == message["reasoning_content"]
    assert history_message["tool_calls"][0]["function"]["arguments"] == "{}"
    assert message["tool_calls"][0]["function"]["arguments"] == ('{"query":"unfinished')


def test_assistant_message_for_history_does_not_persist_final_reasoning() -> None:
    history_message = shared_kernel.assistant_message_for_history({
        "content": "Final answer",
        "reasoning_content": "private reasoning",
    })

    assert history_message == {"role": "assistant", "content": "Final answer"}


def test_runtime_context_persists_only_workspace_skill_activation_audit() -> None:
    state = ToolOrchestrationState()
    state.workspace_skills_read.append({
        "id": "skill-1",
        "name": "governed-analysis",
        "content_sha256": "abc123",
        "content_markdown": "# Exact instructions that must not be persisted",
    })

    context = shared_kernel.runtime_context_payload_from_state(state)

    assert context["workspace_skill_activations"] == [
        {
            "skill_id": "skill-1",
            "content_sha256": "abc123",
            "scope": "turn",
            "activated_by": "model",
        }
    ]
    assert "workspace_skills_read" not in context
    assert "Exact instructions" not in str(context)


def test_shared_kernel_llm_request_failed_payload_uses_latest_successful_execution() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        normalized_sql="SELECT 1",
    )
    state = ToolOrchestrationState(
        generated_sql="SELECT 1",
        selected_database_connector="warehouse",
        metadata_sources={"runtime"},
        context_tables=["warehouse.default.dm_orders"],
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT 1",
                connector_name="warehouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    payload = shared_kernel.llm_request_failed_payload(
        state,
        "example-llm",
        TimeoutError("upstream timed out"),
    )
    assert payload["answer"] == (
        "LLM-модель коннектора «example-llm» вернула ошибку: TimeoutError: upstream timed out"
    )
    assert payload["selected_database_connector"] == "warehouse"
    assert payload["sql"] == "SELECT 1"
    assert payload["execution"]["execution_id"] == "exec-1"


def test_deterministic_finalizer_answer_uses_execution_preview_not_internal_instruction() -> None:
    execution = ExecuteSqlResponse(
        execution_id="exec-1",
        status="succeeded",
        engine="clickhouse",
        preview=QueryPreview(
            columns=["order_date", "order_count"],
            rows=[["2026-05-01", 10], ["2026-05-02", 12]],
        ),
    )
    state = ToolOrchestrationState(
        execution=execution,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT order_date, order_count FROM dm.orders",
                connector_name="clickhouse",
                execution=execution,
                preflight_status="pass",
            )
        ],
    )

    answer = shared_kernel.deterministic_finalizer_answer(state)

    assert "Use execution" not in answer
    assert "grounded result" not in answer
    assert "| order_date | order_count |" in answer
    assert "| 2026-05-01 | 10 |" in answer


def test_shared_kernel_semantic_note_appends_note() -> None:
    state = ToolOrchestrationState(
        semantic_grounding=SemanticGrounding(
            chosen_table_fqn="warehouse.analytics.dm.sales_orders",
            selection_rationale="Selected governed source.",
            candidates=[
                SemanticGroundingCandidate(
                    fqn="warehouse.analytics.dm.sales_orders",
                    service="warehouse",
                    database="analytics",
                    schema="dm",
                    relevance_score=99.0,
                    matched_on=["name_exact"],
                    reason_codes=[
                        "governance_strength",
                        "business_layer_preference",
                        "preferred_connector_map_match",
                    ],
                    certification="Certification.Gold",
                    tier="Tier.Tier1",
                    owners=["analytics"],
                    domains=["sales"],
                    usage_count=42,
                    suspicious=False,
                ),
                SemanticGroundingCandidate(
                    fqn="warehouse.analytics.raw.sales_orders",
                    service="warehouse",
                    database="analytics",
                    schema="raw",
                    relevance_score=87.0,
                    matched_on=["name_exact"],
                    reason_codes=[],
                    certification=None,
                    tier=None,
                    owners=[],
                    domains=[],
                    usage_count=1,
                    suspicious=False,
                ),
            ],
        )
    )

    context = shared_kernel.runtime_context_payload_from_state(state)
    assert context["semantic_grounding"]["chosen_table_fqn"] == (
        "warehouse.analytics.dm.sales_orders"
    )
