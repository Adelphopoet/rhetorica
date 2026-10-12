"""Tests for bounded operational conversation state."""

from rhetorica_backend.domain.chat.conversation.session_state import (
    OperationalConversationState,
    build_operational_state_from_messages,
    merge_operational_states,
    operational_state_from_payload,
)


def _msg(
    role: str,
    content: str,
    *,
    generated_sql: str | None = None,
    execution_id: str | None = None,
    warnings: list[str] | None = None,
    capability_trace: dict | None = None,
):
    return type(
        "Message",
        (),
        {
            "role": role,
            "content": content,
            "generated_sql": generated_sql,
            "execution_id": execution_id,
            "warnings": warnings or [],
            "capability_trace": capability_trace or {},
        },
    )()


def test_operational_state_does_not_infer_semantics_from_user_or_sql() -> None:
    messages = [
        _msg("user", "Причем тут late orders? Нужен retention от IB"),
        _msg(
            "assistant",
            "ok",
            generated_sql=(
                "SELECT corr(ideal_orders, late_orders) AS corr_ib_late FROM dm.orders_daily_report"
            ),
            execution_id="exec-1",
        ),
    ]

    state = build_operational_state_from_messages(messages)

    assert state.to_payload() == {
        "last_successful_execution_id": "exec-1",
        "state_revision": 0,
        "transitions": ["last_successful_execution_updated"],
    }
    assert "active_metrics" not in state.to_payload()
    assert "active_dimensions" not in state.to_payload()
    assert "entity_scope" not in state.to_payload()


def test_operational_state_ignores_failed_execution() -> None:
    state = build_operational_state_from_messages([
        _msg(
            "assistant",
            "failed",
            execution_id="exec-failed",
            warnings=["sql_execution_failed"],
        )
    ])

    assert state.last_successful_execution_id is None


def test_operational_state_persists_exact_tool_activation_manifest() -> None:
    state = build_operational_state_from_messages([
        _msg(
            "assistant",
            "done",
            capability_trace={
                "runtime_context": {
                    "tool_activation": {
                        "group_names": ["data_analysis", "mcp_connectors"],
                        "mcp_tools": [
                            {
                                "connector_name": "jira-retail",
                                "remote_tool_names": ["search_issues"],
                            },
                            {
                                "connector_name": "jira-logistics",
                                "remote_tool_names": ["search_issues"],
                            },
                        ],
                    }
                }
            },
        )
    ])

    assert state.to_payload()["tool_activation"] == {
        "group_names": ["data_analysis", "mcp_connectors"],
        "mcp_tools": [
            {
                "connector_name": "jira-retail",
                "remote_tool_names": ["search_issues"],
            },
            {
                "connector_name": "jira-logistics",
                "remote_tool_names": ["search_issues"],
            },
        ],
    }


def test_legacy_semantic_payload_is_dropped_on_read() -> None:
    state = operational_state_from_payload({
        "active_metrics": ["retention", "late_orders"],
        "active_dimensions": ["store"],
        "entity_scope": ["store_format=БФ"],
        "time_range": "2025-01-01..2026-01-01",
        "selected_connector": "clickhouse",
        "last_successful_execution_id": "exec-1",
        "state_revision": 7,
        "transitions": ["metrics_updated_from_sql"],
    })

    assert state.to_payload() == {
        "last_successful_execution_id": "exec-1",
        "state_revision": 7,
        "transitions": [],
    }


def test_operational_merge_is_bounded_and_replaces_execution_pointer() -> None:
    persisted = OperationalConversationState(
        last_successful_execution_id="exec-old",
        state_revision=4,
        transitions=[f"transition-{index}" for index in range(30)],
    )
    update = OperationalConversationState(
        last_successful_execution_id="exec-new",
        transitions=["last_successful_execution_updated"],
    )

    merged = merge_operational_states(persisted=persisted, update=update)

    assert merged.last_successful_execution_id == "exec-new"
    assert merged.state_revision == 5
    assert len(merged.transitions) == 22
    assert merged.transitions[-2:] == [
        "last_successful_execution_updated",
        "state_revision_incremented",
    ]
