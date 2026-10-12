"""Tests for LLM client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient, LlmTurnRequest


@pytest.fixture
def llm_connection():
    """Create test LLM connection config."""
    return LlmConnectionConfig(
        base_url="https://api.openai.com/v1",
        model="gpt-4",
        api_key="test-key",
        endpoint_path="/chat/completions",
        temperature=0.7,
        max_tokens=1000,
        verify_tls=True,
    )


@pytest.fixture
def llm_client(llm_connection):
    """Create LLM client."""
    return LlmClient(llm_connection)


class TestLlmClient:
    """Tests for LlmClient."""

    @pytest.mark.parametrize("store", [None, False, True])
    @pytest.mark.parametrize("responses", [False, True])
    def test_direct_transport_sends_optional_store(self, llm_connection, store, responses):
        llm_connection.extra_settings = {} if store is None else {"store": store}
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(
            body={
                "id": "resp-test",
                "output_text": "ok",
                "choices": [{"message": {"content": "ok"}}],
            },
        )
        with patch("httpx.Client") as http_client_cls:
            http_client_cls.return_value.__enter__.return_value = http_client
            client = LlmClient(llm_connection)
            messages = [{"role": "user", "content": "hi"}]
            if responses:
                client.responses_completion(messages=messages)
            else:
                client.chat_completion(messages=messages)
        payload = http_client.post.call_args.kwargs["json"]
        if store is None:
            assert "store" not in payload
        else:
            assert payload["store"] is store

    def test_build_endpoint_with_path(self, llm_client):
        """Test endpoint building with relative path."""
        result = llm_client._build_endpoint("https://api.example.com/", "/chat/completions")
        assert result == "https://api.example.com/chat/completions"

    def test_build_endpoint_with_absolute_url(self, llm_client):
        """Test endpoint building with absolute URL."""
        result = llm_client._build_endpoint(
            "https://api.example.com/",
            "https://other-api.example.com/chat/completions",
        )
        assert result == "https://other-api.example.com/chat/completions"

    def test_build_headers_with_api_key(self, llm_connection):
        """Test headers building with API key."""
        headers = LlmClient._build_headers(llm_connection)
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-key"

    def test_build_headers_with_multiple_api_keys(self, monkeypatch):
        """Test headers pick one configured API key."""
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            api_keys=["test-key-1", "test-key-2"],
            verify_tls=True,
        )
        monkeypatch.setattr("secrets.choice", lambda items: items[1])

        headers = LlmClient._build_headers(connection)

        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-key-2"

    def test_build_headers_can_pick_api_key_by_index(self):
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            api_keys=["test-key-1", "test-key-2"],
            verify_tls=True,
        )

        headers = LlmClient._build_headers(connection, api_key_index=3)

        assert headers["Authorization"] == "Bearer test-key-2"

    def test_build_headers_without_api_key(self):
        """Test headers building without API key."""
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            verify_tls=True,
        )
        headers = LlmClient._build_headers(connection)
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_build_payload_with_tools(self, llm_connection):
        """Test payload building with tools."""
        messages = [{"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        payload = LlmClient._build_payload(llm_connection, messages, tools)

        assert payload["model"] == "gpt-4"
        assert payload["messages"] == messages
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 1000

    def test_chat_payload_uses_configured_reasoning_effort(self, llm_connection):
        llm_connection.reasoning_effort = "high"

        payload = LlmClient._build_payload(llm_connection, [{"role": "user", "content": "hi"}])

        assert payload["reasoning_effort"] == "high"

    def test_build_payload_with_tools_honors_tool_choice(self, llm_connection):
        messages = [{"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        payload = LlmClient._build_payload(
            llm_connection,
            messages,
            tools,
            tool_choice="none",
        )

        assert payload["tool_choice"] == "none"

    def test_build_payload_with_tools_honors_exact_function_choice(self, llm_connection):
        messages = [{"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "submit_final_answer"}}]
        tool_choice = {
            "type": "function",
            "function": {"name": "submit_final_answer"},
        }

        payload = LlmClient._build_payload(
            llm_connection,
            messages,
            tools,
            tool_choice=tool_choice,
        )

        assert payload["tool_choice"] == tool_choice

    def test_build_payload_uses_max_completion_tokens_when_configured(self):
        connection = LlmConnectionConfig(
            base_url="https://api.example.com/v1",
            model="reasoning-model",
            max_tokens=12_000,
            max_tokens_parameter="max_completion_tokens",
        )

        payload = LlmClient._build_payload(
            connection,
            [{"role": "user", "content": "Hello"}],
        )

        assert payload["max_completion_tokens"] == 12_000
        assert "max_tokens" not in payload

    def test_build_payload_defaults_to_max_tokens(self):
        connection = LlmConnectionConfig(
            base_url="https://api.example.com/v1",
            model="legacy-model",
            max_tokens=900,
        )

        payload = LlmClient._build_payload(
            connection,
            [{"role": "user", "content": "Hello"}],
        )

        assert payload["max_tokens"] == 900
        assert "max_completion_tokens" not in payload

    def test_build_payload_without_tools(self, llm_connection):
        """Test payload building without tools."""
        messages = [{"role": "user", "content": "Hello"}]

        payload = LlmClient._build_payload(llm_connection, messages, tools=None)

        assert payload["model"] == "gpt-4"
        assert payload["messages"] == messages
        assert "tools" not in payload
        assert "tool_choice" not in payload

    def test_build_payload_sets_chat_template_thinking_mode(self, llm_connection):
        llm_connection.extra_settings["chat_template_enable_thinking"] = False

        payload = LlmClient._build_payload(
            llm_connection,
            [{"role": "user", "content": "Hello"}],
        )

        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_build_payload_omits_chat_template_kwargs_by_default(self, llm_connection):
        payload = LlmClient._build_payload(
            llm_connection,
            [{"role": "user", "content": "Hello"}],
        )

        assert "chat_template_kwargs" not in payload

    def test_build_payload_with_response_schema(self, llm_connection):
        messages = [{"role": "user", "content": "Hello"}]
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

        payload = LlmClient._build_payload(
            llm_connection,
            messages,
            tools=None,
            response_schema=schema,
        )

        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["response_format"]["json_schema"]["schema"] == schema

    def test_build_payload_does_not_mix_tools_and_response_schema(self, llm_connection):
        messages = [{"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}

        payload = LlmClient._build_payload(
            llm_connection,
            messages,
            tools=tools,
            response_schema=schema,
        )

        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"
        assert "response_format" not in payload

    def test_build_payload_enables_reasoning_split_for_examplecopilot(self):
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="ExampleReasoningModel",
            verify_tls=True,
        )

        payload = LlmClient._build_payload(
            connection,
            [{"role": "user", "content": "Hello"}],
        )

        assert payload["reasoning_split"] is True

    def test_extract_message(self):
        """Test message extraction from response."""
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello!",
                    }
                }
            ]
        }
        message = LlmClient._extract_message(body)
        assert message["role"] == "assistant"
        assert message["content"] == "Hello!"

    def test_extract_message_empty_choices(self):
        """Test message extraction with empty choices."""
        body = {"choices": []}
        message = LlmClient._extract_message(body)
        assert message == {}

    def test_is_tools_not_supported_error_400(self):
        """Test tools not supported detection for 400 error."""
        import httpx

        response = httpx.Response(
            status_code=400,
            text="tool_choice is not supported",
        )
        exc = httpx.HTTPStatusError("error", request=None, response=response)
        assert LlmClient._is_tools_not_supported_error(exc) is True

    def test_is_tools_not_supported_error_500(self):
        """Test tools not supported detection for 500 error."""
        import httpx

        response = httpx.Response(
            status_code=500,
            text="Internal server error",
        )
        exc = httpx.HTTPStatusError("error", request=None, response=response)
        assert LlmClient._is_tools_not_supported_error(exc) is False

    def test_is_structured_output_not_supported_error_400(self):
        import httpx

        response = httpx.Response(
            status_code=400,
            text="response_format json_schema is not supported",
        )
        exc = httpx.HTTPStatusError("error", request=None, response=response)
        assert LlmClient._is_structured_output_not_supported_error(exc) is True

    def test_structured_output_enabled_default_true(self, llm_connection):
        assert LlmClient._structured_output_enabled(llm_connection) is True

    def test_structured_output_disabled_by_extra_settings(self, llm_connection):
        llm_connection.extra_settings["structured_output_enabled"] = False
        assert LlmClient._structured_output_enabled(llm_connection) is False

    def test_explicit_final_answer_is_opt_in(self, llm_connection):
        assert LlmClient._explicit_final_answer_enabled(llm_connection) is False

        llm_connection.extra_settings["require_explicit_final_answer"] = True

        assert LlmClient._explicit_final_answer_enabled(llm_connection) is True

    def test_chat_completion_retries_429_with_next_api_key(self):
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            api_keys=["test-key-1", "test-key-2"],
            endpoint_path="/chat/completions",
            verify_tls=True,
            extra_settings={"llm_retry_attempts": 2},
        )
        client = LlmClient(connection)
        http_client = MagicMock()
        http_client.post.side_effect = [
            _fake_response(status_code=429, text="rate limit"),
            _fake_response(body={"choices": [{"message": {"content": "ok"}}]}),
        ]

        with (
            patch("httpx.Client") as http_client_cls,
            patch("secrets.randbelow", return_value=0),
            patch("rhetorica_backend.domain.chat.error_contract.time.sleep"),
        ):
            http_client_cls.return_value.__enter__.return_value = http_client

            response = client.chat_completion(messages=[{"role": "user", "content": "hi"}])

        assert response.message["content"] == "ok"
        auth_headers = [
            call.kwargs["headers"]["Authorization"] for call in http_client.post.call_args_list
        ]
        assert auth_headers == ["Bearer test-key-1", "Bearer test-key-2"]

    def test_chat_completion_uses_configured_request_timeout(self, llm_connection):
        client = LlmClient(llm_connection, request_timeout=120.0)
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(
            body={"choices": [{"message": {"content": "ok"}}]},
        )

        with patch("httpx.Client") as http_client_cls:
            http_client_cls.return_value.__enter__.return_value = http_client

            client.chat_completion(messages=[{"role": "user", "content": "hi"}])

        http_client_cls.assert_called_once_with(timeout=120.0, verify=True)

    def test_file_tools_force_content_logging_off(self, llm_connection):
        llm_connection.extra_settings["log_llm_content"] = True
        client = LlmClient(llm_connection)
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(
            body={"choices": [{"message": {"content": "ok"}}]},
        )
        file_tools = [{"type": "function", "function": {"name": "run_file_code"}}]

        with (
            patch("httpx.Client") as http_client_cls,
            patch(
                "rhetorica_backend.domain.chat.llm.llm_client.build_llm_request_log",
                return_value={},
            ) as request_log,
            patch(
                "rhetorica_backend.domain.chat.llm.llm_client.build_llm_response_log",
                return_value={},
            ) as response_log,
        ):
            http_client_cls.return_value.__enter__.return_value = http_client
            client.chat_completion(
                messages=[{"role": "user", "content": "private file prompt"}],
                tools=file_tools,
            )

        assert request_log.call_args.kwargs["include_content"] is False
        assert response_log.call_args.kwargs["include_content"] is False

    def test_responses_completion_retries_429_with_next_api_key(self):
        connection = LlmConnectionConfig(
            base_url="https://api.openai.com/v1",
            model="gpt-4",
            api_keys=["test-key-1", "test-key-2"],
            verify_tls=True,
            extra_settings={"llm_retry_attempts": 2},
        )
        client = LlmClient(connection)
        http_client = MagicMock()
        http_client.post.side_effect = [
            _fake_response(status_code=429, text="rate limit"),
            _fake_response(body={"id": "resp-1", "output_text": "ok"}),
        ]

        with (
            patch("httpx.Client") as http_client_cls,
            patch("secrets.randbelow", return_value=0),
            patch("rhetorica_backend.domain.chat.error_contract.time.sleep"),
        ):
            http_client_cls.return_value.__enter__.return_value = http_client

            response = client.responses_completion(messages=[{"role": "user", "content": "hi"}])

        assert response.output_text == "ok"
        auth_headers = [
            call.kwargs["headers"]["Authorization"] for call in http_client.post.call_args_list
        ]
        assert auth_headers == ["Bearer test-key-1", "Bearer test-key-2"]

    def test_responses_payload_uses_configured_reasoning_effort(self, llm_connection):
        llm_connection.reasoning_effort = "medium"
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(body={"id": "resp-1", "output_text": "ok"})

        with patch("httpx.Client") as http_client_cls:
            http_client_cls.return_value.__enter__.return_value = http_client
            LlmClient(llm_connection).responses_completion(messages=[{"role": "user", "content": "hi"}])

        assert http_client.post.call_args.kwargs["json"]["reasoning"] == {"effort": "medium"}

    def test_responses_completion_uses_configured_request_timeout(
        self,
        llm_connection,
    ):
        client = LlmClient(llm_connection, request_timeout=120.0)
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(
            body={"id": "resp-1", "output_text": "ok"},
        )

        with patch("httpx.Client") as http_client_cls:
            http_client_cls.return_value.__enter__.return_value = http_client

            client.responses_completion(messages=[{"role": "user", "content": "hi"}])

        http_client_cls.assert_called_once_with(timeout=120.0, verify=True)

    def test_create_turn_normalizes_output_text_and_tool_calls(self, llm_connection):
        client = LlmClient(llm_connection)
        http_client = MagicMock()
        http_client.post.return_value = _fake_response(
            body={
                "choices": [
                    {
                        "message": {
                            "content": "answer",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_sql",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        with patch("httpx.Client") as http_client_cls:
            http_client_cls.return_value.__enter__.return_value = http_client

            result = client.create_turn(
                LlmTurnRequest(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[{"type": "function", "function": {"name": "run_sql"}}],
                    tool_choice="auto",
                )
            )

        assert result.output_text == "answer"
        assert result.tool_calls[0]["id"] == "call-1"
        assert result.tools_disabled is False
        assert result.structured_disabled is False


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        body: dict | None = None,
    ):
        self.status_code = status_code
        self.text = text
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(
            status_code=self.status_code,
            text=self.text,
            request=request,
        )
        raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._body


def _fake_response(
    *,
    status_code: int = 200,
    text: str = "",
    body: dict | None = None,
) -> _FakeResponse:
    return _FakeResponse(status_code=status_code, text=text, body=body)


@pytest.mark.parametrize("top_p", [None, 0.0, 0.95])
def test_auxiliary_chat_payload_top_p(top_p):
    connection = LlmConnectionConfig(base_url="https://llm.example/v1", model="test", top_p=top_p)
    payload = LlmClient._build_payload(connection, [{"role": "user", "content": "test"}])
    assert ("top_p" in payload) == (top_p is not None)
    if top_p is not None:
        assert payload["top_p"] == top_p
