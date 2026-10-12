"""Tests for SQL router."""

import pytest
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import EngineConnectionConfig, ExecuteSqlResponse
from rhetorica_backend.domain.chat.sql.sql_router import SqlRouter


@pytest.fixture
def sql_router():
    """Create SQL router with mock dependencies."""
    mock_execution_service = None
    mock_db_repo = None
    return SqlRouter(
        execution_service=mock_execution_service,
        db_connector_repository=mock_db_repo,
    )


class TestSqlRouter:
    """Tests for SqlRouter."""

    def test_select_connector_by_request_name(self, sql_router):
        """Test connector selection by request name."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db1",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
            ConnectorItem(
                id="2",
                name="db2",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db2",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(
            question="Test",
            workspace_id="test",
            database_connector_name="db2",
        )

        selected = sql_router.select_connector(
            request=request,
            llm_payload={},
            db_connectors=connectors,
        )

        assert selected is not None
        assert selected.name == "db2"

    def test_select_connector_by_llm_target_name(self, sql_router):
        """Test connector selection by LLM target name."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db1",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
            ConnectorItem(
                id="2",
                name="db2",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db2",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(question="Test", workspace_id="test")
        llm_payload = {"target": {"connector_name": "db1"}}

        selected = sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=connectors,
        )

        assert selected is not None
        assert selected.name == "db1"

    def test_select_connector_returns_none_without_explicit_selection(self, sql_router):
        """Without explicit connector name or LLM target, returns None (no fallback)."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db1",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(question="Test", workspace_id="test")
        llm_payload = {}

        selected = sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=connectors,
        )

        assert selected is None

    def test_select_connector_multiple_connectors_no_target_returns_none(self, sql_router):
        """Multiple connectors without explicit selection must return None."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db1",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
            ConnectorItem(
                id="2",
                name="db2",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db2",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(question="Test", workspace_id="test")
        llm_payload = {}

        selected = sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=connectors,
        )

        assert selected is None

    def test_execute_sql_uses_five_minute_default_timeout(self) -> None:
        class _ExecutionServiceStub:
            def __init__(self) -> None:
                self.request = None

            def execute(self, request):
                self.request = request
                return ExecuteSqlResponse(
                    execution_id="exec-1",
                    status="succeeded",
                    engine="clickhouse",
                )

        execution_service = _ExecutionServiceStub()
        router = SqlRouter(
            execution_service=execution_service,
            db_connector_repository=None,
        )
        connection = EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="analyst",
            database="analytics",
        )

        result = router.execute_sql(
            sql="SELECT 1",
            connection=connection,
            workspace_id="default",
            dialect="clickhouse",
        )

        assert result.status == "succeeded"
        assert execution_service.request is not None
        assert execution_service.request.policy.timeout_ms == 300_000

    def test_execute_sql_uses_connector_query_timeout_seconds(self) -> None:
        class _ExecutionServiceStub:
            def __init__(self) -> None:
                self.request = None

            def execute(self, request):
                self.request = request
                return ExecuteSqlResponse(
                    execution_id="exec-1",
                    status="succeeded",
                    engine="clickhouse",
                )

        execution_service = _ExecutionServiceStub()
        router = SqlRouter(
            execution_service=execution_service,
            db_connector_repository=None,
        )
        connection = EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="analyst",
            database="analytics",
            extra_settings={"query_timeout_seconds": 900},
        )

        result = router.execute_sql(
            sql="SELECT 1",
            connection=connection,
            workspace_id="default",
            dialect="clickhouse",
        )

        assert result.status == "succeeded"
        assert execution_service.request is not None
        assert execution_service.request.policy.timeout_ms == 900_000

    def test_select_connector_does_not_fallback_for_unknown_llm_target_name(self, sql_router):
        """Unknown explicit LLM connector target must not fallback to first connector."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="db1",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(question="Test", workspace_id="test")
        llm_payload = {"target": {"connector_name": "missing-db"}}

        selected = sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=connectors,
        )

        assert selected is None

    def test_select_connector_does_not_fallback_for_unmapped_llm_target(self, sql_router):
        """Explicit service/database/schema target must not fallback when unavailable."""
        from datetime import datetime

        connectors = [
            ConnectorItem(
                id="1",
                name="db1",
                workspace_id="test",
                connection=EngineConnectionConfig(
                    engine="clickhouse",
                    host="localhost",
                    port=5432,
                    username="user",
                    password=None,
                    database="analytics",
                ),
                has_password=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ),
        ]
        request = ChatAskRequest(question="Test", workspace_id="test")
        llm_payload = {
            "target": {
                "service": "greenplum-prod",
                "database": "analytics",
                "schema": "sandbox",
            }
        }

        selected = sql_router.select_connector(
            request=request,
            llm_payload=llm_payload,
            db_connectors=connectors,
        )

        assert selected is None

    def test_select_connector_no_connectors(self, sql_router):
        """Test connector selection with no connectors."""
        request = ChatAskRequest(question="Test", workspace_id="test")

        selected = sql_router.select_connector(
            request=request,
            llm_payload={},
            db_connectors=[],
        )

        assert selected is None

    def test_to_optional_str_valid(self, sql_router):
        """Test optional string conversion with valid input."""
        assert sql_router._to_optional_str("hello") == "hello"
        assert sql_router._to_optional_str("  hello  ") == "hello"

    def test_to_optional_str_empty(self, sql_router):
        """Test optional string conversion with empty input."""
        assert sql_router._to_optional_str("") is None
        assert sql_router._to_optional_str("   ") is None
        assert sql_router._to_optional_str(None) is None
        assert sql_router._to_optional_str(123) is None
