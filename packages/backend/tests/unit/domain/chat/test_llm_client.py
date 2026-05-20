"""Tests for LLM client."""

import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient


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

    def test_build_endpoint_with_path(self, llm_client):
        """Test endpoint building with relative path."""
        result = llm_client._build_endpoint(
            "https://api.example.com/", "/chat/completions"
        )
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

    def test_build_payload_with_required_tool_choice(self, llm_connection):
        messages = [{"role": "user", "content": "Hello"}]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        payload = LlmClient._build_payload(
            llm_connection,
            messages,
            tools,
            tool_choice="required",
        )

        assert payload["tool_choice"] == "required"

    def test_build_payload_without_tools(self, llm_connection):
        """Test payload building without tools."""
        messages = [{"role": "user", "content": "Hello"}]

        payload = LlmClient._build_payload(llm_connection, messages, tools=None)

        assert payload["model"] == "gpt-4"
        assert payload["messages"] == messages
        assert "tools" not in payload
        assert "tool_choice" not in payload

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
