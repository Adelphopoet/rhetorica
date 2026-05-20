from __future__ import annotations

import json
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.llm.llm_client import (
    LlmFunctionCall,
    LlmResponsesResult,
)
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.strict_responses_runtime import (
    StrictResponsesRuntime,
)


def _final_payload(answer: str = "Готово.") -> str:
    return json.dumps(
        {
            "schema_version": "v1",
            "answer_markdown": answer,
            "sql": None,
            "target": None,
            "claims": [],
            "evidence": [],
            "claim_evidence_links": [],
            "uncertainty_flags": [],
            "clarification_request": None,
            "analysis_document": None,
            "visualization": None,
        },
        ensure_ascii=False,
    )


def _tool(name: str = "run_sql") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test tool",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
    }


def _jira_create_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_jira_issue",
            "description": "test jira create tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "request_kind": {
                        "type": "string",
                        "enum": ["bug_report", "feature_request", "task"],
                    },
                },
                "required": ["summary", "description", "request_kind"],
                "additionalProperties": False,
            },
        },
    }


class _ResponsesClient:
    def __init__(self, responses: list[LlmResponsesResult]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def responses_completion(self, **kwargs: Any) -> LlmResponsesResult:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("unexpected LLM call")
        return self._responses.pop(0)

    def collect_usages(self) -> list[Any]:
        return []


class _ToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {
            "ok": True,
            "tool_name": tool_name,
            "summary": "executed",
            "can_continue": True,
        }


class _FailingCreateJiraExecutor:
    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        _ = arguments
        assert tool_name == "create_jira_issue"
        return {
            "ok": False,
            "error_code": "jira_issue_type_unavailable",
            "user_message": "В этом Jira project нет подходящего issue type.",
            "can_continue": False,
            "needs_user_input": True,
        }


class _SuccessfulCreateJiraExecutor:
    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        _ = arguments
        assert tool_name == "create_jira_issue"
        return {
            "ok": True,
            "issue_key": "DWH-123",
            "url": "https://jira.example/browse/DWH-123",
            "project_key": "DWH",
            "assignee": {"name": "analyst_user", "display_name": "Alex Analyst"},
            "issue_type": "Задача",
        }


def _response(
    *,
    text: str = "",
    calls: list[LlmFunctionCall] | None = None,
    response_id: str = "resp-1",
) -> LlmResponsesResult:
    return LlmResponsesResult(
        response_id=response_id,
        output_text=text,
        function_calls=list(calls or []),
        raw_body={},
    )


def test_strict_runtime_accepts_schema_valid_final_payload() -> None:
    client = _ResponsesClient([_response(text=_final_payload("Ответ готов."))])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="hi"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["answer"] == "Ответ готов."
    assert client.calls[0]["response_schema"]["title"] == "ChatFinalResponseContractV1"


def test_strict_runtime_accepts_single_fenced_json_provider_wrapper() -> None:
    wrapped = (
        "<think>provider private reasoning</think>\n\n```json\n"
        f"{_final_payload('Привет! Чем могу помочь?')}\n```"
    )
    client = _ResponsesClient([_response(text=wrapped)])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="привет"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["answer"] == "Привет! Чем могу помочь?"
    assert len(client.calls) == 1


def test_strict_runtime_accepts_single_balanced_json_provider_wrapper() -> None:
    wrapped = (
        "<think>provider private reasoning</think>\n\n"
        f"{_final_payload('Привет! Чем могу помочь?')}"
    )
    client = _ResponsesClient([_response(text=wrapped)])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="привет"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["answer"] == "Привет! Чем могу помочь?"
    assert len(client.calls) == 1


def test_strict_runtime_executes_native_tool_call_and_returns_output() -> None:
    executor = _ToolExecutor()
    client = _ResponsesClient([
        _response(
            calls=[
                LlmFunctionCall(
                    call_id="call-1",
                    name="run_sql",
                    arguments_json='{"sql":"select 1"}',
                )
            ],
            response_id="resp-tool",
        ),
        _response(text=_final_payload("SQL выполнен."), response_id="resp-final"),
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="run"),
        db_connectors=[],
        messages=[],
        tools=[_tool()],
        state=ToolOrchestrationState(),
        tool_executor=executor,
    )

    assert result.llm_payload["answer"] == "SQL выполнен."
    assert executor.calls == [("run_sql", {"sql": "select 1"})]
    assert client.calls[1]["input_items"][0]["type"] == "function_call_output"
    assert client.calls[1]["previous_response_id"] == "resp-tool"


def test_strict_runtime_rejects_unknown_tool_without_textual_repair() -> None:
    client = _ResponsesClient([
        _response(
            calls=[
                LlmFunctionCall(
                    call_id="call-1",
                    name="unknown_tool",
                    arguments_json="{}",
                )
            ],
        )
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="run"),
        db_connectors=[],
        messages=[],
        tools=[_tool()],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["uncertainty_flags"] == ["llm_tool_contract_violation"]
    assert len(client.calls) == 1


def test_strict_runtime_rejects_duplicate_native_tool_call() -> None:
    executor = _ToolExecutor()
    client = _ResponsesClient([
        _response(
            calls=[
                LlmFunctionCall(
                    call_id="call-1",
                    name="run_sql",
                    arguments_json='{"sql":"select 1"}',
                ),
                LlmFunctionCall(
                    call_id="call-2",
                    name="run_sql",
                    arguments_json='{"sql":"select 1"}',
                ),
            ],
        )
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="run"),
        db_connectors=[],
        messages=[],
        tools=[_tool()],
        state=ToolOrchestrationState(),
        tool_executor=executor,
    )

    assert result.llm_payload["uncertainty_flags"] == ["llm_tool_contract_violation"]
    assert executor.calls == [("run_sql", {"sql": "select 1"})]


def test_strict_runtime_stops_after_failed_jira_create_before_final_answer() -> None:
    client = _ResponsesClient([
        _response(
            calls=[
                LlmFunctionCall(
                    call_id="call-1",
                    name="create_jira_issue",
                    arguments_json=json.dumps(
                        {
                            "summary": "Удалить витрины",
                            "description": "Удалить витрины юнит экономики",
                            "request_kind": "task",
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
        ),
        _response(text=_final_payload("Задача успешно создана.")),
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="создай задачу"),
        db_connectors=[],
        messages=[],
        tools=[_jira_create_tool()],
        state=ToolOrchestrationState(),
        tool_executor=_FailingCreateJiraExecutor(),
    )

    assert result.llm_payload["answer"] == "В этом Jira project нет подходящего issue type."
    assert result.llm_payload["uncertainty_flags"] == ["jira_issue_type_unavailable"]
    assert len(client.calls) == 1


def test_strict_runtime_passes_created_jira_issue_key_to_llm() -> None:
    client = _ResponsesClient([
        _response(
            calls=[
                LlmFunctionCall(
                    call_id="call-1",
                    name="create_jira_issue",
                    arguments_json=json.dumps(
                        {
                            "summary": "Удалить витрины",
                            "description": "Удалить витрины юнит экономики",
                            "request_kind": "task",
                        },
                        ensure_ascii=False,
                    ),
                )
            ],
            response_id="resp-tool",
        ),
        _response(text=_final_payload("Создана DWH-123."), response_id="resp-final"),
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="создай задачу"),
        db_connectors=[],
        messages=[],
        tools=[_jira_create_tool()],
        state=ToolOrchestrationState(),
        tool_executor=_SuccessfulCreateJiraExecutor(),
    )

    tool_output = json.loads(client.calls[1]["input_items"][0]["output"])
    compact_payload = tool_output["compact_payload"]
    assert compact_payload["tool_name"] == "create_jira_issue"
    assert compact_payload["issue_key"] == "DWH-123"
    assert compact_payload["url"] == "https://jira.example/browse/DWH-123"
    assert result.llm_payload["answer"] == "Создана DWH-123."


def test_strict_runtime_repairs_invalid_final_payload_once() -> None:
    client = _ResponsesClient([
        _response(text="not json"),
        _response(text=_final_payload("Починил контракт.")),
    ])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="q"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["answer"] == "Починил контракт."
    assert len(client.calls) == 2
    assert client.calls[1]["tools"] == []


def test_strict_runtime_fails_closed_when_repair_is_invalid() -> None:
    client = _ResponsesClient([_response(text="bad"), _response(text="still bad")])

    result = StrictResponsesRuntime().run(
        llm_client=client,  # type: ignore[arg-type]
        request=ChatAskRequest(question="q"),
        db_connectors=[],
        messages=[],
        tools=[],
        state=ToolOrchestrationState(),
        tool_executor=_ToolExecutor(),
    )

    assert result.llm_payload["uncertainty_flags"] == ["llm_final_payload_invalid"]
    assert result.llm_payload["visualization"] is None
