"""Explicit LLM runtime contract checks for strict Responses orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NativeResponsesToolingContract:
    """Resolved native Responses tooling contract for a connector."""

    supported: bool
    responses_endpoint_configured: bool
    tool_calls_supported: bool
    connector_endpoint_path: str


class LlmRuntimeContractResolver:
    """Resolve strict runtime compatibility from explicit connector contract."""

    @classmethod
    def resolve_native_responses_tooling(
        cls,
        *,
        connection: Any,
        profile: Any,
    ) -> NativeResponsesToolingContract:
        endpoint_path = cls._connector_endpoint_path(connection)
        return NativeResponsesToolingContract(
            supported=(
                cls._responses_endpoint_configured(connection)
                and cls._tool_calls_supported(connection=connection, profile=profile)
            ),
            responses_endpoint_configured=cls._responses_endpoint_configured(connection),
            tool_calls_supported=cls._tool_calls_supported(
                connection=connection,
                profile=profile,
            ),
            connector_endpoint_path=endpoint_path,
        )

    @staticmethod
    def build_native_responses_tooling_error(
        *,
        connector_name: str | None,
        contract: NativeResponsesToolingContract,
    ) -> str:
        connector_label = (
            f'LLM connector "{connector_name}"'
            if isinstance(connector_name, str) and connector_name.strip()
            else "LLM connector"
        )
        blockers: list[str] = []
        if not contract.responses_endpoint_configured:
            blockers.append(
                "configured for chat completions "
                f"({contract.connector_endpoint_path}) instead of native Responses"
            )
        if not contract.tool_calls_supported:
            blockers.append("does not declare `support_tool_calls=true`")
        if not blockers:
            return (
                f"{connector_label} does not satisfy the native Responses tooling "
                "contract required for this request."
            )
        blockers_text = " and ".join(blockers)
        return (
            f"{connector_label} is {blockers_text}. "
            "This request requires native Responses tools. "
            "Reconfigure it to use `/responses` (or set `responses_endpoint_path`) "
            "and enable `support_tool_calls=true`."
        )

    @staticmethod
    def _responses_endpoint_configured(connection: Any) -> bool:
        extra_settings = LlmRuntimeContractResolver._extra_settings(connection)
        responses_endpoint_path = extra_settings.get("responses_endpoint_path")
        if isinstance(responses_endpoint_path, str) and responses_endpoint_path.strip():
            return True
        return (
            LlmRuntimeContractResolver._connector_endpoint_path(connection)
            .rstrip("/")
            == "/responses"
        )

    @staticmethod
    def _tool_calls_supported(
        *,
        connection: Any,
        profile: Any,
    ) -> bool:
        extra_settings = LlmRuntimeContractResolver._extra_settings(connection)
        declared_support = extra_settings.get("support_tool_calls")
        if isinstance(declared_support, bool):
            return declared_support
        return LlmRuntimeContractResolver._profile_supports(profile, "tool_calls")

    @staticmethod
    def _connector_endpoint_path(connection: Any) -> str:
        raw = getattr(connection, "endpoint_path", "/responses")
        if not isinstance(raw, str):
            return "/responses"
        value = raw.strip()
        return value or "/responses"

    @staticmethod
    def _extra_settings(connection: Any) -> dict[str, Any]:
        extra_settings = getattr(connection, "extra_settings", {})
        if isinstance(extra_settings, dict):
            return extra_settings
        return {}

    @staticmethod
    def _profile_supports(profile: Any, capability: str) -> bool:
        if profile is None:
            return False
        supports = getattr(profile, "supports", ())
        for item in supports:
            if getattr(item, "value", None) == capability:
                return True
            if item == capability:
                return True
        return False
