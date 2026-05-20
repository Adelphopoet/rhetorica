"""Tests for LLM usage tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.llm.llm_client import LlmCallUsage, LlmClient


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


class TestLlmCallUsage:
    """Tests for LlmCallUsage dataclass."""

    def test_create_llm_call_usage(self):
        """Test creating an LLM call usage record."""
        usage = LlmCallUsage(
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            duration_ms=1234.5,
            retries=0,
        )
        assert usage.model == "gpt-4"
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150
        assert usage.duration_ms == 1234.5
        assert usage.retries == 0
        assert usage.phase == "tool_loop"

    def test_llm_call_usage_is_frozen(self):
        """Test that LlmCallUsage is immutable."""
        usage = LlmCallUsage(
            model="gpt-4",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            duration_ms=2000.0,
            retries=1,
        )
        with pytest.raises(AttributeError):
            usage.model = "planner"


class TestLlmClientUsageCapture:
    """Tests for LLM client usage tracking."""

    def test_chat_completion_captures_usage(self, llm_client):
        """Test that chat_completion captures usage from response."""
        mock_response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer": "test"}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_body
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            response = llm_client.chat_completion(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert response.message.get("content") == '{"answer": "test"}'
        usages = llm_client.collect_usages()
        assert len(usages) == 1
        assert usages[0].prompt_tokens == 100
        assert usages[0].completion_tokens == 50
        assert usages[0].total_tokens == 150
        assert usages[0].model == "gpt-4"
        assert usages[0].phase == "tool_loop"

    def test_collect_usages_can_label_phase(self, llm_client):
        """Test that finalizer/repair callers can tag collected usage."""
        mock_response_body = {
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_body
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            llm_client.chat_completion(messages=[{"role": "user", "content": "hello"}])

        usages = llm_client.collect_usages(phase="finalizer")
        assert len(usages) == 1
        assert usages[0].phase == "finalizer"

    def test_collect_usages_returns_and_clears(self, llm_client):
        """Test that collect_usages returns and clears the list."""
        mock_response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer": "test"}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_body
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            # Make two calls
            llm_client.chat_completion(messages=[{"role": "user", "content": "hello"}])
            llm_client.chat_completion(messages=[{"role": "user", "content": "hello"}])

        # Should have two usages
        usages = llm_client.collect_usages()
        assert len(usages) == 2
        assert all(u.model == "gpt-4" for u in usages)

        # After collecting, list should be empty
        usages_after = llm_client.collect_usages()
        assert len(usages_after) == 0

    def test_multiple_calls_accumulate_usages(self, llm_client):
        """Test that multiple calls accumulate usages."""
        for i in range(3):
            mock_response_body = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f'{{"answer": "test{i}"}}',
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100 * (i + 1),
                    "completion_tokens": 50 * (i + 1),
                    "total_tokens": 150 * (i + 1),
                },
            }

            with patch("httpx.Client") as mock_client:
                mock_response = MagicMock()
                mock_response.json.return_value = mock_response_body
                mock_response.raise_for_status = MagicMock()
                mock_response.status_code = 200
                mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
                mock_client.return_value.__exit__ = MagicMock(return_value=False)
                mock_client.post.return_value = mock_response

                llm_client.chat_completion(messages=[{"role": "user", "content": f"hello{i}"}])

        usages = llm_client.collect_usages()
        assert len(usages) == 3
        # Check that each has different token counts
        assert usages[0].total_tokens == 150
        assert usages[1].total_tokens == 300
        assert usages[2].total_tokens == 450

    def test_usage_without_usage_in_response(self, llm_client):
        """Test handling response without usage field."""
        mock_response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer": "test"}',
                    }
                }
            ],
            # No usage field
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_body
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            response = llm_client.chat_completion(
                messages=[{"role": "user", "content": "hello"}]
            )

        # Should still work, but usage is recorded with zero tokens
        assert response.message.get("content") == '{"answer": "test"}'
        usages = llm_client.collect_usages()
        assert len(usages) == 1
        assert usages[0].prompt_tokens == 0
        assert usages[0].completion_tokens == 0
        assert usages[0].total_tokens == 0

    def test_usage_with_different_models(self, llm_client):
        """Test that different models are captured correctly."""
        mock_response_body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer": "test"}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_body
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response

            # The connection model is "gpt-4", should be captured
            llm_client.chat_completion(messages=[{"role": "user", "content": "hello"}])

        usages = llm_client.collect_usages()
        assert len(usages) == 1
        assert usages[0].model == "gpt-4"
