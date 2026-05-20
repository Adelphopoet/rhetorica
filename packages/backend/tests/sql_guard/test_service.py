from rhetorica_backend.contracts import SqlGuardRequest
from rhetorica_backend.sql_guard.service import SqlGuardService

service = SqlGuardService()


def test_guard_applies_default_limit() -> None:
    decision = service.guard(SqlGuardRequest(sql="SELECT city FROM sales", dialect="clickhouse"))

    assert decision.allowed is True
    assert decision.applied_row_limit == 5000
    assert decision.normalized_sql is not None
    assert "LIMIT 5000" in decision.normalized_sql.upper()
    assert decision.violations == []


def test_guard_does_not_break_impala_relationless_select() -> None:
    decision = service.guard(SqlGuardRequest(sql="SELECT 1", dialect="hive", engine="impala"))

    assert decision.allowed is True
    assert decision.applied_row_limit is None
    assert decision.normalized_sql == "SELECT 1"
    assert decision.warnings == []
    assert decision.violations == []


def test_guard_keeps_impala_table_select_bounded() -> None:
    decision = service.guard(
        SqlGuardRequest(sql="SELECT city FROM sales", dialect="hive", engine="impala")
    )

    assert decision.allowed is True
    assert decision.applied_row_limit == 5000
    assert decision.normalized_sql is not None
    assert decision.normalized_sql.upper() == "SELECT CITY FROM SALES LIMIT 5000"
    assert decision.warnings == ["Applied default LIMIT 5000."]
    assert decision.violations == []


def test_guard_clamps_limit_above_policy() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="SELECT city FROM sales LIMIT 20000",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is True
    assert decision.applied_row_limit == 5000
    assert decision.normalized_sql is not None
    assert "LIMIT 5000" in decision.normalized_sql.upper()
    assert decision.warnings


def test_guard_blocks_non_read_only_statement() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="INSERT INTO sales_archive SELECT * FROM sales",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is False
    assert decision.normalized_sql is None
    assert decision.violations[0].code == "NON_READ_ONLY"


def test_guard_blocks_multi_statement_payload() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="SELECT 1; SELECT 2",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is False
    assert decision.violations[0].code == "MULTI_STATEMENT"


def test_guard_allows_describe_statement() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="DESCRIBE dm.ft_order",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is True
    assert decision.normalized_sql == "DESCRIBE dm.ft_order"
    assert decision.applied_row_limit is None
    assert decision.violations == []


def test_guard_allows_show_command_introspection() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="SHOW DATABASES",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is True
    assert decision.normalized_sql == "SHOW DATABASES"
    assert decision.applied_row_limit is None
    assert decision.violations == []


def test_guard_blocks_non_introspection_command() -> None:
    decision = service.guard(
        SqlGuardRequest(
            sql="SET max_threads = 1",
            dialect="clickhouse",
        )
    )

    assert decision.allowed is False
    assert decision.normalized_sql is None
    assert decision.violations[0].code == "NON_READ_ONLY"
