"""Tests for chat settings."""

import os
from unittest import mock

from rhetorica_backend.domain.chat.settings import (
    ChatSettings,
    get_chat_settings,
    get_max_context_tables,
    get_max_iterations,
    get_message_limit,
)


class TestChatSettings:
    """Test ChatSettings configuration."""

    def test_default_values(self) -> None:
        """Test default settings values."""
        settings = ChatSettings()
        assert settings.max_iterations == 100
        assert settings.default_message_limit == 20
        assert settings.max_context_tables == 20
        assert settings.max_metadata_connectors == 100
        assert settings.max_db_connectors == 500
        assert settings.llm_request_timeout == 45.0
        assert settings.verify_tls is True

    def test_max_iterations_from_env(self) -> None:
        """Test max_iterations can be overridden via environment variable."""
        with mock.patch.dict(os.environ, {"CHAT_MAX_ITERATIONS": "50"}):
            # Need to reload module to pick up new env var
            import importlib

            from rhetorica_backend.domain.chat import settings as settings_module

            importlib.reload(settings_module)

            settings = settings_module.ChatSettings()
            assert settings.max_iterations == 50

            # Restore original module
            importlib.reload(settings_module)

    def test_custom_values(self) -> None:
        """Test custom settings values."""
        settings = ChatSettings(
            max_iterations=30,
            default_message_limit=50,
            max_context_tables=10,
            max_metadata_connectors=200,
            max_db_connectors=1000,
            llm_request_timeout=60.0,
            verify_tls=False,
        )
        assert settings.max_iterations == 30
        assert settings.default_message_limit == 50
        assert settings.max_context_tables == 10
        assert settings.max_metadata_connectors == 200
        assert settings.max_db_connectors == 1000
        assert settings.llm_request_timeout == 60.0
        assert settings.verify_tls is False


class TestChatSettingsHelpers:
    """Test helper functions for chat settings."""

    def test_get_max_iterations(self) -> None:
        """Test get_max_iterations returns correct value."""
        result = get_max_iterations()
        assert isinstance(result, int)
        assert result == 100

    def test_get_message_limit(self) -> None:
        """Test get_message_limit returns correct value."""
        result = get_message_limit()
        assert isinstance(result, int)
        assert result == 20

    def test_get_max_context_tables(self) -> None:
        """Test get_max_context_tables returns correct value."""
        result = get_max_context_tables()
        assert isinstance(result, int)
        assert result == 20

    def test_get_chat_settings_cached(self) -> None:
        """Test get_chat_settings returns cached instance."""
        settings1 = get_chat_settings()
        settings2 = get_chat_settings()
        assert settings1 is settings2
