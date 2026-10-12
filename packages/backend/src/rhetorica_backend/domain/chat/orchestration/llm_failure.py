"""User-facing LLM failure details."""

from __future__ import annotations


class LlmFailureMessageFormatter:
    """Formats transparent, bounded LLM transport errors for analysts."""

    _MAX_ERROR_MESSAGE_CHARS = 2_000

    @classmethod
    def format(
        cls,
        *,
        connector_name: str | None,
        error: Exception,
    ) -> str:
        normalized_connector_name = (connector_name or "").strip() or "неизвестный"
        error_type = type(error).__name__
        error_message = " ".join(str(error).split()) or error_type
        if len(error_message) > cls._MAX_ERROR_MESSAGE_CHARS:
            error_message = f"{error_message[: cls._MAX_ERROR_MESSAGE_CHARS - 1]}…"
        return (
            f"LLM-модель коннектора «{normalized_connector_name}» вернула ошибку: "
            f"{error_type}: {error_message}"
        )
