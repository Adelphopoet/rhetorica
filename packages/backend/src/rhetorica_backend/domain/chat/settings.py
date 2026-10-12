"""Chat service configuration constants."""

from __future__ import annotations

from pydantic import Field
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
    max_iterations: int = 100  # Maximum LLM tool-orchestration turns
    default_message_limit: int = 20  # Default conversation history message limit
    context_compaction_enabled: bool = True
    context_compaction_trigger_ratio: float = Field(default=0.8, gt=0, lt=1)

    # HTTP timeouts
    llm_request_timeout: float = 300.0  # Timeout for LLM API requests
    tool_execution_timeout: float = 60.0  # Timeout for a single tool call

    # Conversation-scoped raw tool artifacts. Promoted evidence is retained separately.
    tool_artifact_ttl_hours: int = 168
    tool_artifact_max_chars: int = 500_000
    tool_artifact_max_records_per_conversation: int = 100

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
