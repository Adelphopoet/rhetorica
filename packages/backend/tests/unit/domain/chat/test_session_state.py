"""Tests for semantic session state extraction."""

from rhetorica_backend.domain.chat.conversation.session_state import (
    build_session_state_from_messages,
    extract_runtime_context_from_history,
)


def _msg(
    role: str,
    content: str,
    generated_sql: str | None = None,
    selected_connector: str | None = None,
):
    return type(
        "Message",
        (),
        {
            "role": role,
            "content": content,
            "generated_sql": generated_sql,
            "selected_connector": selected_connector,
        },
    )()


def test_session_state_extracts_metric_dimension_and_time_range() -> None:
    messages = [
        _msg("user", "Покажи GMV по region за 2025"),
        _msg(
            "assistant",
            "ok",
            generated_sql="SELECT region, sum(gmv) AS gmv FROM analytics.sales GROUP BY 1",
            selected_connector="clickhouse-main",
        ),
    ]

    state = build_session_state_from_messages(messages)

    assert "gmv" in state.active_metrics
    assert "region" in state.active_dimensions
    assert state.time_range == "за 2025"
    assert state.selected_connector == "clickhouse-main"


def test_session_state_resets_on_user_command() -> None:
    messages = [
        _msg("user", "Покажи revenue по country"),
        _msg("user", "начнем заново"),
        _msg("user", "Покажи MAU"),
    ]

    state = build_session_state_from_messages(messages)

    assert "mau" in state.active_metrics
    assert "revenue" not in state.active_metrics
    assert "reset_by_user_command" in state.transitions


def test_session_state_keeps_metric_for_followup_refinement() -> None:
    messages = [
        _msg("user", "Покажи GMV по country за 2025"),
        _msg("user", "А теперь только darkstore по region?"),
    ]

    state = build_session_state_from_messages(messages)

    assert "gmv" in state.active_metrics
    assert "country" in state.active_dimensions
    assert "region" in state.active_dimensions
    assert "darkstore" in state.entity_scope
    assert "metric_not_explicit" not in state.open_ambiguities


def test_session_state_extracts_scope_and_time_range_from_sql() -> None:
    messages = [
        _msg(
            "assistant",
            "ok",
            generated_sql=(
                "SELECT region, sum(gmv) AS gmv_total "
                "FROM analytics.sales "
                "WHERE city = 'Moscow' "
                "AND dt BETWEEN '2025-01-01' AND '2025-01-31' "
                "GROUP BY region"
            ),
        ),
    ]

    state = build_session_state_from_messages(messages)

    assert "gmv_total" in state.active_metrics
    assert "region" in state.active_dimensions
    assert state.time_range == "2025-01-01..2025-01-31"
    assert "city=Moscow" in state.entity_scope


def test_extract_runtime_context_from_history_digest() -> None:
    history = [
        {"role": "assistant", "content": "Ответ"},
        {
            "role": "assistant",
            "content": (
                "Context digest:\n"
                "selected_connector=clickhouse-main\n"
                "recent_tables=Sample-ClickHouse.default.dm.ft_main_whs_partner_date, "
                "Sample-ClickHouse.default.dm.ft_order_delivery_sample_d1_agg\n"
                "table_columns.Sample-ClickHouse.default.dm.ft_order_delivery_sample_d1_agg=order_id|cm2_amt|create_date\n"
                'semantic_candidate={"certification":"Certification.Gold","database":"default","domains":["sales"],"fqn":"Sample-ClickHouse.default.dm.ft_main_whs_partner_date","matched_on":["name_exact","business_schema"],"owners":["analytics"],"reason_codes":["governance_strength","business_layer_preference"],"relevance_score":98.0,"schema":"dm","service":"Sample-ClickHouse","suspicious":false,"tier":"Tier.Tier1","usage_count":120}\n'
                "semantic_chosen_table=Sample-ClickHouse.default.dm.ft_main_whs_partner_date\n"
                "semantic_selection_rationale=Selected `Sample-ClickHouse.default.dm.ft_main_whs_partner_date` because it has strong governance signals, business-layer schema.\n"
                "active_metrics=gmv, orders\n"
                "active_dimensions=region, city\n"
                "time_range=2025-01-01..2025-01-31\n"
                "entity_scope=darkstore, city=Moscow\n"
                "confirmed_assumptions=assume gross orders | assume local timezone\n"
                "open_ambiguities=metric_not_explicit\n"
            ),
        },
    ]

    context = extract_runtime_context_from_history(history)

    assert context.selected_connector == "clickhouse-main"
    assert context.recent_tables == [
        "Sample-ClickHouse.default.dm.ft_main_whs_partner_date",
        "Sample-ClickHouse.default.dm.ft_order_delivery_sample_d1_agg",
    ]
    assert context.metadata_table_columns == {
        "sample-clickhouse.default.dm.ft_order_delivery_sample_d1_agg": {
            "order_id",
            "cm2_amt",
            "create_date",
        }
    }
    assert context.active_metrics == ["gmv", "orders"]
    assert context.active_dimensions == ["region", "city"]
    assert context.time_range == "2025-01-01..2025-01-31"
    assert context.entity_scope == ["darkstore", "city=Moscow"]
    assert context.semantic_grounding is not None
    assert (
        context.semantic_grounding.chosen_table_fqn
        == "Sample-ClickHouse.default.dm.ft_main_whs_partner_date"
    )
    assert context.semantic_grounding.candidates[0].schema_name == "dm"
    assert context.confirmed_assumptions == [
        "assume gross orders",
        "assume local timezone",
    ]
    assert context.open_ambiguities == ["metric_not_explicit"]
