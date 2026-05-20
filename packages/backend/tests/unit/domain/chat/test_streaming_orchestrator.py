import json
from datetime import UTC, datetime

from rhetorica_backend.contracts.chat import ChatAskRequest, ChunkEvent, ThinkingEvent
from rhetorica_backend.contracts.connectors import ConnectorItem
from rhetorica_backend.contracts.execution import (
    EngineConnectionConfig,
    ExecuteSqlResponse,
    QueryPreview,
)
from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.chat_orchestrator import (
    ChatOrchestrator,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    final_response_json_schema,
)
from rhetorica_backend.domain.chat.orchestration.state import (
    SqlExecutionAttempt,
    ToolOrchestrationState,
)
from rhetorica_backend.domain.chat.orchestration.streaming_orchestrator import (
    StreamingChatOrchestrator,
    StreamingToolState,
)


class _FakeCompletionResponse:
    def __init__(self, content: str):
        self.message = {"content": content}


class _RepairingLlmClient:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def chat_completion_with_fallback(self, **kwargs: object):
        self.calls.append(kwargs)
        schema = kwargs.get("response_schema")
        if isinstance(schema, dict) and schema.get("required") == [
            "should_plan",
            "plan",
            "warnings",
        ]:
            return (
                _FakeCompletionResponse(
                    json.dumps({
                        "should_plan": False,
                        "plan": None,
                        "warnings": [],
                    })
                ),
                False,
                False,
            )
        if not self._responses:
            raise AssertionError("No fake repair responses left")
        return _FakeCompletionResponse(self._responses.pop(0)), False, False


def _chat_orchestrator() -> ChatOrchestrator:
    return ChatOrchestrator(
        llm_connector_repository=None,
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=None,
        conversation_manager=None,
        db_connector_repository=None,
    )


def _orchestrator() -> StreamingChatOrchestrator:
    return StreamingChatOrchestrator(
        llm_connector_repository=None,
        openmetadata_connector_repository=None,
        metadata_fetcher=None,
        sql_router=None,
        conversation_manager=None,
        db_connector_repository=None,
    )


def test_process_content_extracts_think_blocks_for_both_tag_variants() -> None:
    orchestrator = _orchestrator()
    content = (
        "<think>plan first</think>\n"
        "<thinking>then execute</thinking>\n"
        '{"answer":"Done","sql":null,"target":{}}'
    )

    events = orchestrator._process_content_for_thinking(content)

    assert len(events) == 3
    assert isinstance(events[0], ThinkingEvent)
    assert events[0].content == "plan first"
    assert isinstance(events[1], ThinkingEvent)
    assert events[1].content == "then execute"
    assert isinstance(events[2], ChunkEvent)
    assert "<think>" not in events[2].content
    assert "<thinking>" not in events[2].content


def test_parse_json_payload_handles_think_and_json_fence() -> None:
    content = """
<think>internal reasoning</think>
```json
{"answer":"Привет!","sql":null,"target":{"connector_name":"dwh"}}
```
    """.strip()

    parsed, parsed_ok = StreamingChatOrchestrator._parse_json_payload(content)

    assert parsed_ok is True
    assert parsed["answer"] == "Привет!"
    assert parsed["target"] == {"connector_name": "dwh"}


def test_duplicate_tool_call_detection() -> None:
    state = ToolOrchestrationState()
    args_json = '{"sql":"SELECT 1"}'

    first = StreamingChatOrchestrator._is_duplicate_tool_call(
        state=state,
        tool_name="run_sql",
        args_json=args_json,
    )
    second = StreamingChatOrchestrator._is_duplicate_tool_call(
        state=state,
        tool_name="run_sql",
        args_json=args_json,
    )

    assert first is False
    assert second is True


def test_dynamic_tools_enable_run_sql_after_metadata_grounding() -> None:
    state = ToolOrchestrationState()
    state.context_tables = ["dm.ft_main_whs_partner_date"]
    request = ChatAskRequest(
        question="Покажи выручку по городам",
        workspace_id="default",
        run_sql=True,
    )
    connector = ConnectorItem(
        id="connector-1",
        name="clickhouse",
        workspace_id="default",
        connection=EngineConnectionConfig(
            engine="clickhouse",
            host="localhost",
            port=8123,
            username="tester",
        ),
        has_password=False,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    tools = shared_kernel.ensure_dynamic_tools(
        tools=[],
        request=request,
        db_connectors=[connector],
        state=state,
    )

    assert "run_sql" in shared_kernel.tool_names(tools)
    assert "run_sql" in state.capability_state.available_tools


def test_chat_orchestrator_repairs_initial_non_json_payload() -> None:
    orchestrator = _chat_orchestrator()
    state = ToolOrchestrationState()
    llm_client = _RepairingLlmClient(
        [
            json.dumps(
                {
                    "schema_version": "v1",
                    "answer_markdown": "Починил контракт.",
                    "sql": None,
                    "target": None,
                    "claims": [],
                    "evidence": [],
                    "claim_evidence_links": [],
                    "uncertainty_flags": [],
                    "visualization": None,
                },
                ensure_ascii=False,
            )
        ]
    )

    payload = orchestrator._validate_or_repair_final_payload(
        llm_client=llm_client,
        raw_content="Просто текст без JSON",
        state=state,
        response_schema=final_response_json_schema(),
    )

    assert payload["answer"] == "Починил контракт."
    assert state.parse_failure_count == 1
    assert state.schema_repaired == 1
    assert state.schema_failed == 0
    assert state.warnings == []


def test_chat_orchestrator_returns_fallback_after_failed_repair() -> None:
    orchestrator = _chat_orchestrator()
    state = ToolOrchestrationState()
    llm_client = _RepairingLlmClient(["все еще не json"])

    payload = orchestrator._validate_or_repair_final_payload(
        llm_client=llm_client,
        raw_content="совсем мимо схемы",
        state=state,
        response_schema=final_response_json_schema(),
    )

    assert payload["answer"] == (
        "I could not produce a schema-valid answer. "
        "Please retry the question with a narrower scope."
    )
    assert state.parse_failure_count == 2
    assert state.schema_failed == 1
    assert state.warnings == ["llm_response_schema_validation_failed"]
    assert payload["uncertainty_flags"] == ["schema_validation_failed"]


def test_streaming_orchestrator_returns_fallback_after_failed_repair() -> None:
    orchestrator = _orchestrator()
    state = StreamingToolState()
    llm_client = _RepairingLlmClient(["все еще не json"])

    payload = orchestrator._validate_or_repair_final_payload(
        llm_client=llm_client,
        raw_content="совсем мимо схемы",
        state=state,
        response_schema=final_response_json_schema(),
    )

    assert payload["answer"] == (
        "I could not produce a schema-valid answer. "
        "Please retry the question with a narrower scope."
    )
    assert state.parse_failure_count == 2
    assert state.schema_failed == 1
    assert state.warnings == ["llm_response_schema_validation_failed"]
    assert payload["uncertainty_flags"] == ["schema_validation_failed"]


def test_llm_request_failed_payload_preserves_latest_successful_execution() -> None:
    state = StreamingToolState(
        selected_database_connector="trino-adhoc",
        generated_sql="SELECT * FROM iceberg.dm_ft.ft_order LIMIT 10",
        context_tables=["Sample-ClickHouse.default.dm.ft_order"],
        metadata_sources={"openmetadata"},
        warnings=["llm_request_failed"],
    )
    state.execution_attempts.append(
        SqlExecutionAttempt(
            sql="SELECT * FROM dm.ft_order LIMIT 10",
            connector_name="clickhouse",
            execution=ExecuteSqlResponse(
                execution_id="exec-1",
                status="succeeded",
                engine="clickhouse",
                query_id="query-1",
                preview=QueryPreview(columns=["order_id"], rows=[["1"]]),
            ),
        )
    )

    payload = shared_kernel.llm_request_failed_payload(state)

    assert payload["selected_database_connector"] == "clickhouse"
    assert payload["sql"] == "SELECT * FROM dm.ft_order LIMIT 10"
    assert payload["execution"]["execution_id"] == "exec-1"
    assert payload["context_tables"] == ["Sample-ClickHouse.default.dm.ft_order"]
    assert payload["warnings"] == ["llm_request_failed"]


def test_chat_orchestrator_does_not_salvage_intermediate_text_after_repair_meta_error() -> None:
    orchestrator = _chat_orchestrator()
    state = ToolOrchestrationState()
    llm_client = _RepairingLlmClient(
        [
            json.dumps(
                {
                    "error": "No payload provided to fix",
                    "message": "repair meta error",
                },
                ensure_ascii=False,
            )
        ]
    )

    payload = orchestrator._validate_or_repair_final_payload(
        llm_client=llm_client,
        raw_content="Описание ft_order: факт заказов.",
        state=state,
        response_schema=final_response_json_schema(),
    )

    assert payload["answer"] == (
        "I could not produce a schema-valid answer. "
        "Please retry the question with a narrower scope."
    )
    assert state.warnings == ["llm_response_schema_validation_failed"]
    assert payload["uncertainty_flags"] == ["schema_validation_failed"]
