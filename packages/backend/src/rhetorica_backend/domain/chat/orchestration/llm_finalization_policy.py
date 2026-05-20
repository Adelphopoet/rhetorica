"""Shared LLM finalization and repair policy for chat orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.llm.prompt_builder import PromptBuilder
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import (
    FinalPayloadPolicy,
)
from rhetorica_backend.domain.chat.orchestration.final_response_contract import (
    build_final_response_payload,
    final_response_json_schema,
    validate_final_response_payload,
)
from rhetorica_backend.infra.logging import sanitize_for_logging


class LlmFinalizationPolicy:
    """Encapsulates final answer validation, repair, and fallback flows."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        build_finalizer_context: Callable[..., dict[str, Any]],
        build_final_payload_from_parsed: Callable[..., dict[str, Any]],
        fallback_final_payload: Callable[[], dict[str, Any]],
        deterministic_finalizer_answer: Callable[[Any], str],
        execution_recovery_prompt: Callable[..., str],
        extract_message_content: Callable[[dict[str, Any]], str],
        has_needs_user_clarification_flag: Callable[[dict[str, Any]], bool],
        is_acceptable_final_payload: Callable[..., bool],
        merge_uncertainty_flags: Callable[..., list[str]],
        preserve_raw_markdown_answer: Callable[..., dict[str, Any]],
        extract_uncertainty_flags: Callable[[dict[str, Any]], list[str]],
        successful_execution_attempts: Callable[[Any], list[Any]],
        looks_like_nonfinal_raw_payload: Callable[[str | None], bool],
        to_optional_str: Callable[[Any], str | None],
        strip_thinking_tags: Callable[[str], str],
    ) -> None:
        self._logger = logger
        self._build_finalizer_context = build_finalizer_context
        self._build_final_payload_from_parsed = build_final_payload_from_parsed
        self._fallback_final_payload = fallback_final_payload
        self._deterministic_finalizer_answer = deterministic_finalizer_answer
        self._execution_recovery_prompt = execution_recovery_prompt
        self._extract_message_content = extract_message_content
        self._has_needs_user_clarification_flag = has_needs_user_clarification_flag
        self._is_acceptable_final_payload = is_acceptable_final_payload
        self._merge_uncertainty_flags = merge_uncertainty_flags
        self._preserve_raw_markdown_answer = preserve_raw_markdown_answer
        self._extract_uncertainty_flags = extract_uncertainty_flags
        self._successful_execution_attempts = successful_execution_attempts
        self._looks_like_nonfinal_raw_payload = looks_like_nonfinal_raw_payload
        self._to_optional_str = to_optional_str
        self._strip_thinking_tags = strip_thinking_tags

    def validate_final_response_content(
        self,
        *,
        raw_content: str,
        state: Any,
        log_prefix: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        parsed, parsed_ok = FinalPayloadPolicy.parse_json_payload(raw_content)
        if not parsed_ok:
            self._logger.warning(
                f"{log_prefix}.parse_failed",
                extra={"raw_content": sanitize_for_logging(raw_content)},
            )
            state.parse_failure_count += 1
            return None, ["root: invalid JSON payload"]

        validated, errors = validate_final_response_payload(parsed)
        if validated is None:
            self._logger.warning(
                f"{log_prefix}.validation_failed",
                extra={
                    "validation_errors": errors[:10],
                    "raw_content": sanitize_for_logging(raw_content),
                },
            )
            return None, errors
        return validated.to_payload(), []

    def repair_finalizer_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_invalid_output: str,
        validation_errors: list[str],
        raw_final_text: str,
        state: Any,
        uncertainty_flags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        state.add_warning("llm_finalizer_repair_requested")
        repair_context = {
            "raw_invalid_finalizer_output": raw_invalid_output,
            "validation_errors": validation_errors[:10],
            "original_finalizer_context": self._build_finalizer_context(
                raw_final_text=raw_final_text,
                state=state,
                uncertainty_flags=uncertainty_flags,
            ),
        }
        self._logger.info(
            "llm.finalizer.repair_requested",
            extra={
                "validation_errors": validation_errors[:10],
                "raw_invalid_output": sanitize_for_logging(raw_invalid_output),
            },
        )
        try:
            response, tools_disabled, structured_disabled = (
                llm_client.chat_completion_with_fallback(
                    messages=[
                        {
                            "role": "system",
                            "content": PromptBuilder.build_finalizer_repair_system_prompt(),
                        },
                        {
                            "role": "user",
                            "content": PromptBuilder.build_finalizer_repair_user_prompt(
                                repair_context
                            ),
                        },
                    ],
                    tools=[],
                    response_schema=final_response_json_schema(),
                )
            )
        except Exception:
            self._logger.exception("LLM finalizer repair call failed")
            return None
        self._collect_llm_usages(
            llm_client=llm_client,
            state=state,
            phase="repair",
        )

        if tools_disabled:
            state.add_warning("llm_tools_not_supported")
        if structured_disabled:
            state.add_warning("llm_structured_output_not_supported")

        repaired_content = self._extract_message_content(response.message)
        repaired_payload, repaired_errors = self.validate_final_response_content(
            raw_content=repaired_content,
            state=state,
            log_prefix="llm.finalizer.repair",
        )
        if repaired_payload is None:
            self._logger.warning(
                "llm.finalizer.repair_failed",
                extra={
                    "validation_errors": repaired_errors[:10],
                    "repaired_content": sanitize_for_logging(repaired_content),
                },
            )
            return None
        return repaired_payload

    def deterministic_finalizer_fallback(
        self,
        *,
        state: Any,
        raw_final_text: str | None = None,
        uncertainty_flags: list[str] | None = None,
    ) -> dict[str, Any]:
        state.add_warning("llm_finalizer_failed")
        merged_flags = self._merge_uncertainty_flags(
            uncertainty_flags,
            ["finalizer_backend_fallback"],
        )
        raw_text = self._to_optional_str(raw_final_text)
        if raw_text is not None:
            parsed_raw, parsed_raw_ok = FinalPayloadPolicy.parse_json_payload(raw_text)
            if parsed_raw_ok:
                validated_raw, _ = validate_final_response_payload(parsed_raw)
                if validated_raw is not None:
                    parsed_payload = validated_raw.to_payload()
                    parsed_payload["uncertainty_flags"] = self._merge_uncertainty_flags(
                        merged_flags,
                        self._extract_uncertainty_flags(parsed_payload),
                    )
                    return self._build_final_payload_from_parsed(
                        parsed_payload=parsed_payload,
                        fallback_answer=parsed_payload.get("answer"),
                        state=state,
                    )

        answer = self._to_optional_str(raw_final_text)
        if answer is not None:
            answer = self._strip_thinking_tags(answer).strip() or answer
        if self._looks_like_nonfinal_raw_payload(answer):
            answer = None
        if not answer:
            answer = self._deterministic_finalizer_answer(state)

        parsed_payload = build_final_response_payload(
            answer_markdown=answer,
            sql=state.generated_sql,
            uncertainty_flags=merged_flags,
        )
        return self._build_final_payload_from_parsed(
            parsed_payload=parsed_payload,
            fallback_answer=answer,
            state=state,
        )

    def finalize_raw_answer(
        self,
        *,
        llm_client: LlmClient,
        request: ChatAskRequest,
        raw_final_text: str,
        state: Any,
        uncertainty_flags: list[str] | None = None,
    ) -> dict[str, Any]:
        state.add_warning("llm_finalizer_requested")
        parsed_raw_payload, parsed_raw_ok = FinalPayloadPolicy.parse_json_payload(
            raw_final_text
        )
        if parsed_raw_ok and (
            parsed_raw_payload.get("next_action")
            in {"tool_call", "final_answer", "needs_user_clarification"}
            or "tool_request" in parsed_raw_payload
        ):
            return self.deterministic_finalizer_fallback(
                state=state,
                raw_final_text=raw_final_text,
                uncertainty_flags=self._merge_uncertainty_flags(
                    uncertainty_flags,
                    ["schema_validation_failed"],
                ),
            )

        finalizer_context = self._build_finalizer_context(
            raw_final_text=raw_final_text,
            state=state,
            uncertainty_flags=uncertainty_flags,
        )
        try:
            response, tools_disabled, structured_disabled = (
                llm_client.chat_completion_with_fallback(
                    messages=[
                        {
                            "role": "system",
                            "content": PromptBuilder.build_finalizer_system_prompt(),
                        },
                        {
                            "role": "user",
                            "content": PromptBuilder.build_finalizer_user_prompt(
                                finalizer_context
                            ),
                        },
                    ],
                    tools=[],
                    response_schema=final_response_json_schema(),
                )
            )
        except Exception:
            self._logger.exception("LLM finalizer call failed")
            return self.deterministic_finalizer_fallback(
                state=state,
                raw_final_text=raw_final_text,
                uncertainty_flags=uncertainty_flags,
            )
        self._collect_llm_usages(
            llm_client=llm_client,
            state=state,
            phase="finalizer",
        )

        if tools_disabled:
            state.add_warning("llm_tools_not_supported")
        if structured_disabled:
            state.add_warning("llm_structured_output_not_supported")

        finalizer_content = self._extract_message_content(response.message)
        parsed_payload, validation_errors = self.validate_final_response_content(
            raw_content=finalizer_content,
            state=state,
            log_prefix="llm.finalizer",
        )
        if parsed_payload is None:
            parsed_payload = self.repair_finalizer_payload(
                llm_client=llm_client,
                raw_invalid_output=finalizer_content,
                validation_errors=validation_errors,
                raw_final_text=raw_final_text,
                state=state,
                uncertainty_flags=uncertainty_flags,
            )
            if parsed_payload is None:
                state.schema_failed += 1
                state.add_warning("llm_response_schema_validation_failed")
                return self.deterministic_finalizer_fallback(
                    state=state,
                    raw_final_text=raw_final_text,
                    uncertainty_flags=self._merge_uncertainty_flags(
                        uncertainty_flags,
                        ["schema_validation_failed"],
                    ),
                )
            state.schema_repaired += 1
        else:
            state.schema_valid_first_pass += 1

        parsed_payload = self._preserve_raw_markdown_answer(
            raw_final_text=raw_final_text,
            parsed_raw_payload=parsed_raw_payload if parsed_raw_ok else None,
            parsed_payload=parsed_payload,
        )
        parsed_payload["uncertainty_flags"] = self._merge_uncertainty_flags(
            uncertainty_flags,
            self._extract_uncertainty_flags(parsed_payload),
        )
        final_payload = self._build_final_payload_from_parsed(
            parsed_payload=parsed_payload,
            fallback_answer=raw_final_text,
            state=state,
        )
        if self._has_needs_user_clarification_flag(final_payload):
            state.add_warning("llm_user_clarification_requested")
            return final_payload
        if self._is_acceptable_final_payload(
            llm_payload=final_payload,
            state=state,
            request=request,
        ):
            return final_payload
        state.add_warning("llm_unacceptable_final_payload")
        return self.deterministic_finalizer_fallback(
            state=state,
            raw_final_text=raw_final_text,
            uncertainty_flags=self._merge_uncertainty_flags(
                parsed_payload.get("uncertainty_flags"),
                ["finalizer_unacceptable"],
            ),
        )

    def validate_or_repair_final_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_content: str,
        state: Any,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        parsed, parsed_ok = FinalPayloadPolicy.parse_json_payload(raw_content)
        errors: list[str]
        if not parsed_ok:
            self._logger.warning(
                "llm.final_payload.parse_failed",
                extra={"raw_content": sanitize_for_logging(raw_content)},
            )
            state.parse_failure_count += 1
            errors = ["root: invalid JSON payload"]
        else:
            validated, errors = validate_final_response_payload(parsed)
            if validated is not None:
                state.schema_valid_first_pass += 1
                self._logger.info(
                    "llm.final_payload.validated",
                    extra={"payload": sanitize_for_logging(validated.to_payload())},
                )
                return validated.to_payload()

        repaired = self.repair_final_payload(
            llm_client=llm_client,
            raw_content=raw_content,
            validation_errors=errors,
            response_schema=response_schema,
        )
        self._collect_llm_usages(
            llm_client=llm_client,
            state=state,
            phase="repair",
        )
        repaired_parsed, repaired_ok = FinalPayloadPolicy.parse_json_payload(repaired)
        if not repaired_ok:
            self._logger.warning(
                "llm.final_payload.repair_parse_failed",
                extra={"repaired_content": sanitize_for_logging(repaired)},
            )
            state.parse_failure_count += 1
            state.schema_failed += 1
            state.add_warning("llm_response_schema_validation_failed")
            return self._fallback_final_payload()

        repaired_validated, repaired_errors = validate_final_response_payload(
            repaired_parsed
        )
        if repaired_validated is not None:
            state.schema_repaired += 1
            self._logger.info(
                "llm.final_payload.repaired",
                extra={"payload": sanitize_for_logging(repaired_validated.to_payload())},
            )
            return repaired_validated.to_payload()

        self._logger.warning(
            "Final response validation failed after repair: %s",
            "; ".join(repaired_errors[:4]),
        )
        state.schema_failed += 1
        state.add_warning("llm_response_schema_validation_failed")
        return self._fallback_final_payload()

    def repair_final_payload(
        self,
        *,
        llm_client: LlmClient,
        raw_content: str,
        validation_errors: list[str],
        response_schema: dict[str, Any],
    ) -> str:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "Return ONLY valid JSON object for ChatFinalResponseSchema v1. "
                    "No markdown, no prose."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Fix this payload to match schema.\n"
                    f"Validation errors: {validation_errors[:10]}\n"
                    f"Invalid payload:\n{raw_content}"
                ),
            },
        ]
        self._logger.info(
            "llm.final_payload.repair_requested",
            extra={
                "validation_errors": validation_errors[:10],
                "invalid_payload": sanitize_for_logging(raw_content),
            },
        )
        try:
            response, _, _ = llm_client.chat_completion_with_fallback(
                messages=repair_messages,
                tools=[],
                response_schema=response_schema,
            )
        except Exception:
            self._logger.exception("LLM repair call failed")
            return ""

        repaired = self._extract_message_content(response.message)
        self._logger.info(
            "llm.final_payload.repair_completed",
            extra={"repaired_content": sanitize_for_logging(repaired)},
        )
        return repaired

    def attempt_execution_recovery_finalization(
        self,
        *,
        llm_client: LlmClient,
        messages: list[dict[str, Any]],
        tool_result: dict[str, Any],
        state: Any,
        response_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        successful_attempts = self._successful_execution_attempts(state)
        if not successful_attempts:
            return None

        recovery_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "At least one SQL execution already succeeded in this turn. "
                    "Do not call more tools. Return the final answer using the best "
                    "supported successful execution already present in the conversation. "
                    "If you cite execution evidence, use the chosen execution_id as the "
                    "execution_result reference."
                ),
            },
            {
                "role": "user",
                "content": self._execution_recovery_prompt(
                    tool_result=tool_result,
                    successful_attempts=successful_attempts,
                ),
            },
        ]
        try:
            response, tools_disabled, structured_disabled = (
                llm_client.chat_completion_with_fallback(
                    messages=recovery_messages,
                    tools=[],
                    response_schema=response_schema or final_response_json_schema(),
                )
            )
        except Exception:
            self._logger.exception("LLM recovery finalization failed")
            return None
        self._collect_llm_usages(
            llm_client=llm_client,
            state=state,
            phase="finalizer",
        )

        if tools_disabled:
            state.add_warning("llm_tools_not_supported")
        if structured_disabled:
            state.add_warning("llm_structured_output_not_supported")
        state.add_warning("llm_execution_recovery_finalization")

        recovery_content = self._extract_message_content(response.message)
        parsed_payload = self.validate_or_repair_final_payload(
            llm_client=llm_client,
            raw_content=recovery_content,
            state=state,
            response_schema=response_schema,
        )
        if self._has_needs_user_clarification_flag(parsed_payload):
            state.add_warning("llm_user_clarification_requested")
        return self._build_final_payload_from_parsed(
            parsed_payload=parsed_payload,
            fallback_answer=recovery_content,
            state=state,
        )

    def attempt_duplicate_loop_recovery_finalization(
        self,
        *,
        llm_client: LlmClient | None,
        messages: list[dict[str, Any]],
        tool_result: dict[str, Any],
        state: Any,
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if llm_client is None or response_schema is None:
            return None
        successful_attempts = self._successful_execution_attempts(state)
        if not successful_attempts:
            return None
        self._logger.warning(
            "llm.duplicate_tool_loop.recovery_requested",
            extra={
                "successful_execution_count": len(successful_attempts),
                "warnings": list(state.warnings),
            },
        )
        state.add_warning("llm_duplicate_tool_loop_recovery_requested")
        return self.attempt_execution_recovery_finalization(
            llm_client=llm_client,
            messages=messages,
            tool_result=tool_result,
            state=state,
            response_schema=response_schema,
        )

    @staticmethod
    def _collect_llm_usages(
        *,
        llm_client: LlmClient,
        state: Any,
        phase: str,
    ) -> None:
        usages = llm_client.collect_usages(phase=phase)
        state_usages = getattr(state, "llm_usages", None)
        if isinstance(state_usages, list):
            state_usages.extend(usages)
