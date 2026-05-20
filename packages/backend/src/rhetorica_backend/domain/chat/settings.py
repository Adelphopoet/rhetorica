"""Chat service configuration constants."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatSettings(BaseSettings):
    """Chat service settings."""

    model_config = SettingsConfigDict(
        env_prefix="CHAT_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # LLM tool orchestration
    max_iterations: int = 100  # Maximum tool orchestration iterations
    default_message_limit: int = 20  # Default conversation history message limit
    max_context_tables: int = 20  # Maximum context tables to include
    max_metadata_connectors: int = 100  # Maximum metadata connectors to fetch
    max_db_connectors: int = 500  # Maximum database connectors to fetch

    # HTTP timeouts
    llm_request_timeout: float = 45.0  # Timeout for LLM API requests

    # TLS settings
    verify_tls: bool = True  # Verify TLS certificates for LLM requests


# Global settings instance
_settings: ChatSettings | None = None


def get_chat_settings() -> ChatSettings:
    """Get chat settings (cached)."""
    global _settings
    if _settings is None:
        _settings = ChatSettings()
    return _settings


def get_max_iterations() -> int:
    """Get maximum LLM tool orchestration iterations."""
    return get_chat_settings().max_iterations


def get_message_limit() -> int:
    """Get default conversation message limit."""
    return get_chat_settings().default_message_limit


def get_max_context_tables() -> int:
    """Get maximum context tables."""
    return get_chat_settings().max_context_tables
