from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_error_policy import (
    ToolErrorPolicy,
)


def test_should_stop_after_repeated_preflight_fail() -> None:
    state = ToolOrchestrationState(preflight_fail=2)

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={"ok": False, "error_code": "sql_preflight_failed"},
        state=state,
    )

    assert should_stop is True


def test_record_duplicate_tool_error_updates_state() -> None:
    state = ToolOrchestrationState(preflight_uncertain=1)

    ToolErrorPolicy.record_duplicate_tool_error(
        state=state,
        tool_result={"ok": False, "error_code": "sql_preflight_uncertain"},
    )

    assert state.preflight_uncertain == 2
    assert "llm_tool_loop_duplicate_preflight_uncertain" in state.warnings


def test_should_stop_after_repeated_unavailable_context_connector() -> None:
    state = ToolOrchestrationState(unavailable_context_connector_fail=2)

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={
            "ok": False,
            "error_code": "database_connector_unavailable_for_context_table",
        },
        state=state,
    )

    assert should_stop is True


def test_tool_result_user_message_includes_preflight_diagnostics() -> None:
    message = ToolErrorPolicy.tool_result_user_message(
        {
            "ok": False,
            "user_message": "SQL preflight failed.",
            "preflight_errors": [
                {"message": "Column 'gmv' is missing in table 'dm.ft_orders'."},
                {"message": "Table 'dm.ft_orders' is missing in metadata context."},
            ],
        }
    )

    assert message.startswith("SQL preflight failed. Diagnostics:")
    assert "Column 'gmv' is missing" in message
