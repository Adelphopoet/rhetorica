from rhetorica_backend.contracts.execution import ExecuteSqlResponse
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.tool_error_policy import (
    ToolErrorPolicy,
)


def test_should_continue_after_repeated_preflight_fail() -> None:
    state = ToolOrchestrationState(preflight_fail=2)

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={"ok": False, "error_code": "sql_preflight_failed"},
        state=state,
    )

    assert should_stop is False


def test_should_not_stop_recoverable_preflight_after_successful_execution() -> None:
    state = ToolOrchestrationState(
        preflight_uncertain=2,
        execution_attempts=[
            SqlExecutionAttempt(
                sql="SELECT count() FROM system.query_log",
                connector_name="clickhouse",
                execution=ExecuteSqlResponse(
                    execution_id="exec-1",
                    status="succeeded",
                    engine="clickhouse",
                ),
                preflight_status="pass",
            )
        ],
    )

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={"ok": False, "error_code": "sql_preflight_uncertain"},
        state=state,
    )

    assert should_stop is False


def test_should_continue_after_repeated_preflight_uncertain() -> None:
    state = ToolOrchestrationState(preflight_uncertain=2)

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={"ok": False, "error_code": "sql_preflight_uncertain"},
        state=state,
    )

    assert should_stop is False


def test_should_not_stop_recoverable_sql_execution_error() -> None:
    state = ToolOrchestrationState()

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={
            "ok": False,
            "error_code": "sql_execution_failed",
            "can_continue": True,
            "retry_recommended": True,
        },
        state=state,
    )

    assert should_stop is False


def test_should_not_stop_recoverable_tool_timeout() -> None:
    state = ToolOrchestrationState()

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={
            "ok": False,
            "error_code": "tool_timeout",
            "error_category": "TRANSIENT",
            "can_continue": True,
            "retry_recommended": True,
        },
        state=state,
    )

    assert should_stop is False


def test_should_continue_after_repeated_unavailable_context_connector() -> None:
    state = ToolOrchestrationState(unavailable_context_connector_fail=2)

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={
            "ok": False,
            "error_code": "database_connector_unavailable_for_context_table",
        },
        state=state,
    )

    assert should_stop is False


def test_should_stop_after_fatal_tool_error() -> None:
    state = ToolOrchestrationState()

    should_stop = ToolErrorPolicy.should_stop_after_tool_error(
        tool_result={
            "ok": False,
            "error_code": "fatal_tool_error",
            "error_category": "FATAL",
            "can_continue": True,
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
