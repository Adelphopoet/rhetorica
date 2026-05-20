from __future__ import annotations

from types import SimpleNamespace

from rhetorica_backend.domain.chat.orchestration.llm_runtime_contract import (
    LlmRuntimeContractResolver,
)


def test_native_responses_tooling_contract_accepts_explicit_responses_connector() -> None:
    contract = LlmRuntimeContractResolver.resolve_native_responses_tooling(
        connection=SimpleNamespace(
            endpoint_path="/responses",
            extra_settings={"support_tool_calls": True},
        ),
        profile=None,
    )

    assert contract.supported is True
    assert contract.responses_endpoint_configured is True
    assert contract.tool_calls_supported is True


def test_native_responses_tooling_contract_rejects_chat_completions_connector() -> None:
    contract = LlmRuntimeContractResolver.resolve_native_responses_tooling(
        connection=SimpleNamespace(
            endpoint_path="/chat/completions",
            extra_settings={},
        ),
        profile=SimpleNamespace(supports={"structured_output"}),
    )

    assert contract.supported is False
    assert contract.responses_endpoint_configured is False
    assert contract.tool_calls_supported is False


def test_native_responses_tooling_error_explains_missing_contract() -> None:
    message = LlmRuntimeContractResolver.build_native_responses_tooling_error(
        connector_name="default-gpt",
        contract=LlmRuntimeContractResolver.resolve_native_responses_tooling(
            connection=SimpleNamespace(
                endpoint_path="/chat/completions",
                extra_settings={},
            ),
            profile=SimpleNamespace(supports={"structured_output"}),
        ),
    )

    assert "default-gpt" in message
    assert "/chat/completions" in message
    assert "support_tool_calls=true" in message
