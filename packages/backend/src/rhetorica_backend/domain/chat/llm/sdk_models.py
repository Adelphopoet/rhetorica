"""Connector adaptation shared by every Agents SDK entry point."""

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from rhetorica_backend.contracts.admin_integrations import LlmConnectionConfig
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.provider_capabilities import (
    ProviderRuntimeCapabilities,
)
from rhetorica_backend.domain.chat.orchestration.sdk_runtime_contracts import (
    AgentsSdkCapabilityError,
)
from rhetorica_backend.domain.chat.settings import get_chat_settings

from agents import ModelSettings
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.models.reasoning_content_replay import ReasoningContentReplayContext


def _replay_provider_reasoning_content(_context: ReasoningContentReplayContext) -> bool:
    """Preserve explicit provider thinking context across SDK-managed tool turns."""
    return True


def _omit_provider_reasoning_content(_context: ReasoningContentReplayContext) -> bool:
    """Apply an explicitly configured provider contract; unknowns use SDK defaults."""
    return False


def sdk_base_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    normalized_path = parsed.path.rstrip("/")
    suffix = next(
        (
            candidate
            for candidate in ("/chat/completions", "/responses")
            if normalized_path.endswith(candidate)
        ),
        None,
    )
    if suffix is None:
        raise AgentsSdkCapabilityError(f"Unsupported Agents SDK endpoint: {endpoint}")
    base_path = normalized_path[: -len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))


def build_sdk_model(*, connection: LlmConnectionConfig, openai_client: AsyncOpenAI) -> Model:
    endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
    normalized_path = urlsplit(endpoint).path.rstrip("/")
    if normalized_path.endswith("/responses"):
        return OpenAIResponsesModel(
            model=connection.model,
            openai_client=openai_client,
            model_is_explicit=True,
        )
    if normalized_path.endswith("/chat/completions"):
        replay = ProviderRuntimeCapabilities.from_settings(
            connection.extra_settings
        ).reasoning_replay
        return OpenAIChatCompletionsModel(
            model=connection.model,
            openai_client=openai_client,
            should_replay_reasoning_content=(
                (_replay_provider_reasoning_content if replay else _omit_provider_reasoning_content)
                if replay is not None
                else None
            ),
        )
    raise AgentsSdkCapabilityError(f"Unsupported Agents SDK endpoint: {endpoint}")


def build_model_settings(connection: LlmConnectionConfig) -> ModelSettings:
    extra_body: dict[str, Any] = {}
    extra_args: dict[str, Any] = {}
    if LlmClient._reasoning_split_enabled(connection):
        extra_body["reasoning_split"] = True
    enable_thinking = connection.extra_settings.get("chat_template_enable_thinking")
    if isinstance(enable_thinking, bool):
        extra_body["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
    endpoint = LlmClient._build_endpoint(connection.base_url, connection.endpoint_path)
    responses_endpoint = urlsplit(endpoint).path.rstrip("/").endswith("/responses")
    use_max_completion_tokens = (
        not responses_endpoint
        and urlsplit(endpoint).path.rstrip("/").endswith("/chat/completions")
        and connection.max_tokens_parameter == "max_completion_tokens"
    )
    if use_max_completion_tokens and connection.max_tokens is not None:
        extra_args["max_completion_tokens"] = connection.max_tokens
    reasoning = (
        Reasoning(
            effort=connection.reasoning_effort,
            summary="auto" if responses_endpoint and enable_thinking else None,
        )
        if connection.reasoning_effort is not None or (responses_endpoint and enable_thinking)
        else None
    )
    context_management = None
    if connection.extra_settings.get("native_compaction_enabled") is True:
        context_management = [
            {
                "type": "compaction",
                "compact_threshold": connection.extra_settings[
                    "native_compaction_threshold_tokens"
                ],
            }
        ]
    return ModelSettings(
        store=connection.store,
        temperature=connection.temperature,
        top_p=connection.top_p,
        max_tokens=None if use_max_completion_tokens else connection.max_tokens,
        truncation="disabled" if get_chat_settings().context_harness_enabled else "auto",
        reasoning=reasoning,
        context_management=context_management,
        include_usage=True,
        preserve_raw_usage=True,
        timeout=get_chat_settings().llm_request_timeout,
        extra_body=extra_body or None,
        extra_args=extra_args or None,
    )
