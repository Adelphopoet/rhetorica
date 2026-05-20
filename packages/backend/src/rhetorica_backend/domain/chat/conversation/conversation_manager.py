"""Conversation management service - handles conversation history and context."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from rhetorica_backend.domain.chat.conversation.session_state import (
    build_session_state_from_messages,
    contains_reset_command,
    merge_session_states,
    session_state_from_payload,
)
from rhetorica_backend.domain.chat.llm.token_budget import (
    LlmPromptBudgetService,
    LlmTokenEstimator,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository


@dataclass
class _PreparedHistoryMessage:
    index: int
    role: str
    content: str
    tokens: int
    priority: int


class ConversationManager:
    """Manages conversation lifecycle and history."""

    _DEFAULT_CONTEXT_WINDOW_TOKENS = LlmPromptBudgetService.DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS
    _DEFAULT_RESPONSE_RESERVE_TOKENS = 4_000
    _DEFAULT_TOOL_RESERVE_TOKENS = 3_000
    _MAX_MESSAGE_CHARS = 5_000
    _MAX_SQL_DIGEST_CHARS = 800
    _MAX_TABLE_DIGEST = 12
    _MAX_COLUMN_DIGEST_TABLES = 4
    _MAX_STATE_DIGEST_ITEMS = 8

    def __init__(
        self,
        chat_repository: ChatRepository | None = None,
        current_user_id: str | None = None,
    ):
        self._chat_repository = chat_repository
        self._current_user_id = current_user_id
        self._logger = logging.getLogger(__name__)

    def create_conversation(
        self,
        workspace_id: str,
        title: str,
    ) -> Any:
        """
        Create new conversation.

        Args:
            workspace_id: Workspace identifier.
            title: Conversation title.

        Returns:
            Created conversation object.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot create conversation")
            return None

        return self._chat_repository.create_conversation(
            workspace_id=workspace_id,
            title=title,
            owner_user_id=self._current_user_id,
        )

    def get_conversation_with_messages(
        self,
        conversation_id: str,
        message_limit: int = 20,
    ) -> Any | None:
        """
        Get conversation with message history.

        Args:
            conversation_id: Conversation identifier.
            message_limit: Maximum number of messages to retrieve.

        Returns:
            Conversation with messages or None if not found.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot get conversation")
            return None

        return self._chat_repository.get_conversation_with_messages(
            conversation_id=conversation_id,
            message_limit=message_limit,
            owner_user_id=self._current_user_id,
        )

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        generated_sql: str | None = None,
        execution_id: str | None = None,
        context_tables: list[str] | None = None,
        metadata_sources: list[str] | None = None,
        claims: list[dict[str, object]] | None = None,
        evidence: list[dict[str, object]] | None = None,
        claim_evidence_links: list[dict[str, object]] | None = None,
        uncertainty_flags: list[str] | None = None,
        capability_trace: dict[str, object] | None = None,
        optimization_trace: dict[str, object] | None = None,
        selected_connector: str | None = None,
        analysis_document: dict[str, object] | None = None,
        visualization_spec: dict[str, object] | None = None,
        warnings: list[str] | None = None,
    ) -> Any | None:
        """
        Save message to conversation.

        Args:
            conversation_id: Conversation identifier.
            role: Message role (user/assistant).
            content: Message content.
            generated_sql: Generated SQL if any.
            execution_id: Execution ID if SQL was executed.
            context_tables: Context tables referenced.
            metadata_sources: Metadata sources used.
            selected_connector: Selected database connector.
            warnings: Any warnings generated.
        """
        if self._chat_repository is None:
            self._logger.warning("Chat repository not available, cannot save message")
            return None

        return self._chat_repository.save_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            generated_sql=generated_sql,
            execution_id=execution_id,
            context_tables=context_tables or [],
            metadata_sources=metadata_sources or [],
            claims=claims or [],
            evidence=evidence or [],
            claim_evidence_links=claim_evidence_links or [],
            uncertainty_flags=uncertainty_flags or [],
            capability_trace=capability_trace or {},
            optimization_trace=optimization_trace or {},
            selected_connector=selected_connector,
            analysis_document=analysis_document,
            visualization_spec=visualization_spec,
            warnings=warnings or [],
            owner_user_id=self._current_user_id,
        )

    def reset_conversation_state(self, conversation_id: str) -> dict[str, object]:
        """Clear semantic conversation state without deleting message history."""
        if self._chat_repository is None:
            return {}
        return self._chat_repository.update_conversation_state(
            conversation_id=conversation_id,
            conversation_state={
                "state_revision": 0,
                "transitions": ["reset_by_user_command"],
            },
            owner_user_id=self._current_user_id,
        )

    def get_conversation_state(self, conversation_id: str) -> dict[str, Any]:
        """Load compact persisted conversation state."""
        if self._chat_repository is None:
            return {}
        state = self._chat_repository.get_conversation_state(
            conversation_id=conversation_id,
            owner_user_id=self._current_user_id,
        )
        return state if isinstance(state, dict) else {}

    def update_conversation_state_from_turn(
        self,
        *,
        conversation_id: str,
        messages: list[Any],
        checkpoint_digest: dict[str, object] | None = None,
        llm_usages: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Persist deterministic semantic state from the current completed turn."""
        if self._chat_repository is None:
            return {}
        raw_persisted_state = self._chat_repository.get_conversation_state(
            conversation_id=conversation_id,
            owner_user_id=self._current_user_id,
        )
        if any(
            str(getattr(message, "role", "") or "") == "user"
            and contains_reset_command(str(getattr(message, "content", "") or ""))
            for message in messages
        ):
            persisted = session_state_from_payload(None)
        else:
            persisted = session_state_from_payload(raw_persisted_state)
        update = build_session_state_from_messages(messages)
        merged = merge_session_states(persisted=persisted, update=update).to_payload()
        if checkpoint_digest is not None:
            merged["analysis_checkpoint"] = checkpoint_digest
        if isinstance(raw_persisted_state, dict):
            for key in ("analysis_plan", "analysis_worker_results"):
                value = raw_persisted_state.get(key)
                if value is not None:
                    merged[key] = value
        if llm_usages:
            merged["llm_calls"] = llm_usages
        return self._chat_repository.update_conversation_state(
            conversation_id=conversation_id,
            conversation_state=merged,
            owner_user_id=self._current_user_id,
        )

    def update_analysis_runtime_state(
        self,
        *,
        conversation_id: str,
        checkpoint_digest: dict[str, object] | None,
        analysis_plan: dict[str, object] | None = None,
        worker_results: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Persist compact live plan/checkpoint state without raw transcripts."""
        if self._chat_repository is None:
            return {}
        persisted = dict(
            self._chat_repository.get_conversation_state(
                conversation_id=conversation_id,
                owner_user_id=self._current_user_id,
            )
        )
        if checkpoint_digest is not None:
            persisted["analysis_checkpoint"] = checkpoint_digest
        if analysis_plan is not None:
            persisted["analysis_plan"] = analysis_plan
        if worker_results is not None:
            persisted["analysis_worker_results"] = worker_results
        return self._chat_repository.update_conversation_state(
            conversation_id=conversation_id,
            conversation_state=persisted,
            owner_user_id=self._current_user_id,
        )

    def format_history_for_context(
        self,
        conversation_id: str,
        message_limit: int = 20,
        *,
        model_name: str | None = None,
        model_context_window_tokens: int | None = None,
        reserve_response_tokens: int = _DEFAULT_RESPONSE_RESERVE_TOKENS,
        reserve_tool_loop_tokens: int = _DEFAULT_TOOL_RESERVE_TOKENS,
        include_warnings: bool = False,
    ) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[str]]:
        """
        Format conversation history for LLM context.

        Args:
            conversation_id: Conversation identifier.
            message_limit: Maximum number of messages to retrieve.
            model_name: Optional model name for tokenizer selection.
            model_context_window_tokens: Optional context window override.
            reserve_response_tokens: Reserved output token budget.
            reserve_tool_loop_tokens: Reserved tokens for tool loop turns.
            include_warnings: Include context warning codes in response.

        Returns:
            List of message dicts with role and content.
        """
        warnings: list[str] = []
        if self._chat_repository is None:
            return ([], warnings) if include_warnings else []

        conversation = self.get_conversation_with_messages(
            conversation_id=conversation_id,
            message_limit=message_limit,
        )
        if conversation is None:
            return ([], warnings) if include_warnings else []

        estimator = LlmTokenEstimator(model_name=model_name)
        if estimator.used_fallback:
            warnings.append("context_token_estimator_fallback")
        if estimator.unknown_model and model_context_window_tokens is None:
            warnings.append("context_window_unconfirmed")

        budget = self._context_budget(
            model_name=model_name,
            model_context_window_tokens=model_context_window_tokens,
            reserve_response_tokens=reserve_response_tokens,
            reserve_tool_loop_tokens=reserve_tool_loop_tokens,
        )
        conversation_state = getattr(conversation, "conversation_state", None)
        prepared = self._prepare_history_messages(
            messages=conversation.messages,
            estimator=estimator,
            warnings=warnings,
            conversation_state=(
                conversation_state if isinstance(conversation_state, dict) else None
            ),
        )
        selected = self._select_messages_with_budget(
            messages=prepared,
            budget=budget,
            warnings=warnings,
        )

        result = [{"role": item.role, "content": item.content} for item in selected]
        if include_warnings:
            return result, warnings
        return result

    @classmethod
    def _context_budget(
        cls,
        model_name: str | None,
        model_context_window_tokens: int | None,
        reserve_response_tokens: int,
        reserve_tool_loop_tokens: int,
    ) -> int:
        if isinstance(model_context_window_tokens, int) and model_context_window_tokens > 0:
            window = model_context_window_tokens
        else:
            window = cls._model_context_window(model_name)
        reserved = max(0, reserve_response_tokens) + max(0, reserve_tool_loop_tokens)
        return max(512, window - reserved)

    @classmethod
    def _model_context_window(cls, model_name: str | None) -> int:
        return LlmPromptBudgetService.model_context_window(model_name)

    def _prepare_history_messages(
        self,
        messages: list[Any],
        estimator: LlmTokenEstimator,
        warnings: list[str],
        conversation_state: dict[str, Any] | None = None,
    ) -> list[_PreparedHistoryMessage]:
        prepared: list[_PreparedHistoryMessage] = []
        for index, msg in enumerate(messages):
            role = str(getattr(msg, "role", "assistant") or "assistant")
            raw_content = str(getattr(msg, "content", "") or "")
            compacted_content = self._compact_content(raw_content)
            if compacted_content != raw_content and "context_compacted" not in warnings:
                warnings.append("context_compacted")
            token_count = estimator.count_text(compacted_content)
            priority = self._message_priority(msg=msg, index=index, total=len(messages))
            prepared.append(
                _PreparedHistoryMessage(
                    index=index,
                    role=role,
                    content=compacted_content,
                    tokens=token_count,
                    priority=priority,
                )
            )

        digest = self._build_runtime_digest(
            messages=messages,
            conversation_state=conversation_state,
        )
        if digest:
            prepared.append(
                _PreparedHistoryMessage(
                    index=len(prepared),
                    role="assistant",
                    content=digest,
                    tokens=estimator.count_text(digest),
                    priority=1_000,
                )
            )
        return prepared

    def _select_messages_with_budget(
        self,
        messages: list[_PreparedHistoryMessage],
        budget: int,
        warnings: list[str],
    ) -> list[_PreparedHistoryMessage]:
        if not messages:
            return []

        essential_ids: set[int] = set()
        user_indices = [msg.index for msg in messages if msg.role == "user"]
        if user_indices:
            essential_ids.add(user_indices[-1])
            if len(user_indices) > 1:
                essential_ids.add(user_indices[-2])
        digest_candidates = [msg.index for msg in messages if msg.priority >= 1_000]
        if digest_candidates:
            essential_ids.add(digest_candidates[-1])

        selected: list[_PreparedHistoryMessage] = []
        total_tokens = 0

        by_index = {msg.index: msg for msg in messages}
        for idx in sorted(essential_ids):
            msg = by_index.get(idx)
            if msg is None:
                continue
            selected.append(msg)
            total_tokens += msg.tokens

        candidates = [msg for msg in messages if msg.index not in essential_ids]
        candidates.sort(key=lambda msg: (msg.priority, msg.index), reverse=True)

        for candidate in candidates:
            if total_tokens + candidate.tokens > budget:
                if "context_truncated" not in warnings:
                    warnings.append("context_truncated")
                continue
            selected.append(candidate)
            total_tokens += candidate.tokens

        selected.sort(key=lambda msg: msg.index)
        return selected

    @classmethod
    def _compact_content(cls, content: str) -> str:
        if len(content) <= cls._MAX_MESSAGE_CHARS:
            return content
        return f"{content[: cls._MAX_MESSAGE_CHARS]}\n[... truncated for context budget ...]"

    @classmethod
    def _message_priority(cls, msg: Any, index: int, total: int) -> int:
        role = str(getattr(msg, "role", "assistant") or "assistant")
        is_recent = index >= max(0, total - 4)
        generated_sql = getattr(msg, "generated_sql", None)
        selected_connector = getattr(msg, "selected_connector", None)
        context_tables = getattr(msg, "context_tables", None)

        base = 30
        if role == "user":
            base = 220 if is_recent else 170
        elif generated_sql:
            base = 200 if is_recent else 150
        elif selected_connector:
            base = 170 if is_recent else 130
        elif isinstance(context_tables, list) and context_tables:
            base = 150 if is_recent else 120
        elif role == "assistant":
            base = 100 if is_recent else 70

        return base + index

    @classmethod
    def _build_runtime_digest(
        cls,
        messages: list[Any],
        conversation_state: dict[str, Any] | None = None,
    ) -> str | None:
        last_sql: str | None = None
        last_connector: str | None = None
        tables: list[str] = []
        metadata_table_columns: dict[str, set[str]] = {}
        semantic_grounding: dict[str, Any] | None = None
        sql_status = "unknown"

        for msg in messages:
            generated_sql = getattr(msg, "generated_sql", None)
            if isinstance(generated_sql, str) and generated_sql.strip():
                last_sql = generated_sql.strip()
                raw_warnings = getattr(msg, "warnings", None)
                if isinstance(raw_warnings, list) and "sql_execution_failed" in raw_warnings:
                    sql_status = "failed"
                elif getattr(msg, "execution_id", None):
                    sql_status = "executed"
                else:
                    sql_status = "generated"

            selected_connector = getattr(msg, "selected_connector", None)
            if isinstance(selected_connector, str) and selected_connector.strip():
                last_connector = selected_connector.strip()

            raw_tables = getattr(msg, "context_tables", None)
            if isinstance(raw_tables, list):
                for table in raw_tables:
                    if not isinstance(table, str) or not table.strip():
                        continue
                    normalized = table.strip()
                    if normalized in tables:
                        tables.remove(normalized)
                    tables.append(normalized)

            capability_trace = getattr(msg, "capability_trace", None)
            if not isinstance(capability_trace, dict):
                continue
            runtime_context = capability_trace.get("runtime_context")
            if not isinstance(runtime_context, dict):
                continue
            raw_semantic_grounding = runtime_context.get("semantic_grounding")
            if isinstance(raw_semantic_grounding, dict):
                semantic_grounding = raw_semantic_grounding
            raw_table_columns = runtime_context.get("metadata_table_columns")
            if isinstance(raw_table_columns, dict):
                for table_name, raw_columns in raw_table_columns.items():
                    if not isinstance(table_name, str) or not table_name.strip():
                        continue
                    if not isinstance(raw_columns, list):
                        continue
                    normalized_table = table_name.strip().lower()
                    columns = metadata_table_columns.setdefault(normalized_table, set())
                    for column in raw_columns:
                        if isinstance(column, str) and column.strip():
                            columns.add(column.strip().lower())

        digest_lines: list[str] = []
        recent_tables = list(reversed(tables))
        if last_sql:
            trimmed_sql = (
                last_sql[: cls._MAX_SQL_DIGEST_CHARS]
                if len(last_sql) > cls._MAX_SQL_DIGEST_CHARS
                else last_sql
            )
            digest_lines.append(f"last_sql_status={sql_status}")
            digest_lines.append(f"last_sql={trimmed_sql}")
        if last_connector:
            digest_lines.append(f"selected_connector={last_connector}")
        if recent_tables:
            digest_lines.append(
                "recent_tables=" + ", ".join(recent_tables[: cls._MAX_TABLE_DIGEST])
            )
        for table_name in recent_tables[: cls._MAX_COLUMN_DIGEST_TABLES]:
            columns = cls._resolve_digest_columns(
                table_name=table_name,
                metadata_table_columns=metadata_table_columns,
            )
            if not columns:
                continue
            digest_lines.append(
                f"table_columns.{table_name}="
                + "|".join(sorted(columns))
            )
        if isinstance(semantic_grounding, dict):
            chosen_table = semantic_grounding.get("chosen_table_fqn")
            if isinstance(chosen_table, str) and chosen_table.strip():
                digest_lines.append(f"semantic_chosen_table={chosen_table.strip()}")
            rationale = semantic_grounding.get("selection_rationale")
            if isinstance(rationale, str) and rationale.strip():
                digest_lines.append(
                    "semantic_selection_rationale=" + rationale.strip()
                )
            lineage_summary = semantic_grounding.get("lineage_summary")
            if isinstance(lineage_summary, str) and lineage_summary.strip():
                digest_lines.append(
                    "semantic_lineage_summary=" + lineage_summary.strip()
                )
            raw_candidates = semantic_grounding.get("candidates")
            if isinstance(raw_candidates, list):
                for candidate in raw_candidates[:3]:
                    if not isinstance(candidate, dict):
                        continue
                    digest_lines.append(
                        "semantic_candidate="
                        + json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                    )
        semantic_state = session_state_from_payload(conversation_state)
        replay_state = build_session_state_from_messages(messages)
        semantic_state = merge_session_states(
            persisted=semantic_state,
            update=replay_state,
            increment_revision=False,
        )
        if semantic_state.active_metrics:
            digest_lines.append(
                "active_metrics="
                + ", ".join(semantic_state.active_metrics[: cls._MAX_STATE_DIGEST_ITEMS])
            )
        if semantic_state.active_dimensions:
            digest_lines.append(
                "active_dimensions="
                + ", ".join(semantic_state.active_dimensions[: cls._MAX_STATE_DIGEST_ITEMS])
            )
        if semantic_state.time_range:
            digest_lines.append(f"time_range={semantic_state.time_range}")
        if semantic_state.entity_scope:
            digest_lines.append(
                "entity_scope="
                + ", ".join(semantic_state.entity_scope[: cls._MAX_STATE_DIGEST_ITEMS])
            )
        if semantic_state.selected_connector:
            digest_lines.append(f"session_selected_connector={semantic_state.selected_connector}")
        if semantic_state.last_successful_execution_id:
            digest_lines.append(
                f"last_successful_execution_id={semantic_state.last_successful_execution_id}"
            )
        if semantic_state.state_revision:
            digest_lines.append(f"state_revision={semantic_state.state_revision}")
        if semantic_state.confirmed_assumptions:
            digest_lines.append(
                "confirmed_assumptions="
                + " | ".join(semantic_state.confirmed_assumptions[: cls._MAX_STATE_DIGEST_ITEMS])
            )
        if semantic_state.open_ambiguities:
            digest_lines.append(
                "open_ambiguities="
                + ", ".join(semantic_state.open_ambiguities[: cls._MAX_STATE_DIGEST_ITEMS])
            )
        if semantic_state.transitions:
            digest_lines.append(
                "state_transitions="
                + ", ".join(semantic_state.transitions[-cls._MAX_STATE_DIGEST_ITEMS :])
            )
        if isinstance(conversation_state, dict):
            checkpoint = conversation_state.get("analysis_checkpoint")
            if isinstance(checkpoint, dict):
                digest_lines.append(
                    "checkpoint_digest="
                    + json.dumps(checkpoint, ensure_ascii=False, sort_keys=True)
                )
        if not digest_lines:
            return None
        return "Context digest:\n" + "\n".join(digest_lines)

    @staticmethod
    def _resolve_digest_columns(
        *,
        table_name: str,
        metadata_table_columns: dict[str, set[str]],
    ) -> set[str]:
        normalized_table = table_name.strip().lower()
        if not normalized_table:
            return set()
        exact = metadata_table_columns.get(normalized_table)
        if exact:
            return exact

        parts = [part for part in normalized_table.split(".") if part]
        for width in range(len(parts), 0, -1):
            suffix = ".".join(parts[-width:])
            matches = [
                columns
                for known_table, columns in metadata_table_columns.items()
                if known_table == suffix or known_table.endswith(f".{suffix}")
            ]
            if len(matches) == 1:
                return matches[0]
        return set()
