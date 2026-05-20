from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from fastembed import TextEmbedding
from pydantic import ValidationError
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.rag.models import (
    RagGateDecision,
    RagMergeDecision,
    RagPipelineResult,
    RagPipelineTaskRequest,
)
from rhetorica_backend.infra.postgres.chat_repository import ChatMessageItem, ChatRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.rag_connector_repository import RagConnectorRepository
from rhetorica_backend.infra.qdrant.insights_store import QdrantInsightsStore

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class FastEmbedProvider:
    """FastEmbed-based embedding provider."""

    def __init__(self, model_name: str):
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> list[float]:
        for vector in self._model.embed([text]):
            return [float(value) for value in vector.tolist()]
        return []


class RagInsightPipelineService:
    """Build and persist reusable insights from useful analyst feedback."""

    _BLOCKING_WARNING_TOKENS = ("unsafe", "blocked", "violation")
    _DEFAULT_MERGE_SIMILARITY_THRESHOLD = 0.9
    _REUSABLE_KNOWLEDGE_TOKENS = (
        "definition",
        "define",
        "means",
        "formula",
        "calculated",
        "how to analyze",
        "how to debug",
        "how to validate",
        "where to find",
        "where to look",
        "table",
        "tables",
        "column",
        "columns",
        "field",
        "fields",
        "source of truth",
        "lineage",
        "join",
        "grain",
        "dimension",
        "metric definition",
        "analysis pattern",
        "breakdown",
        "segment",
        "cohort",
        "funnel",
        "расшифров",
        "означает",
        "формул",
        "как считать",
        "как анализ",
        "как проверить",
        "где искать",
        "где смотреть",
        "таблиц",
        "таблица",
        "колонк",
        "поле",
        "источник",
        "лайнедж",
        "джойн",
        "грейн",
        "разрез",
        "сегмент",
        "когорт",
        "воронк",
    )
    _TEMPORAL_TOKENS = (
        "за январ",
        "за фев",
        "за март",
        "за апр",
        "за май",
        "за июн",
        "за июл",
        "за авг",
        "за сент",
        "за окт",
        "за ноя",
        "за дек",
        "january",
        "february",
        "march",
        "april",
        "may ",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "this month",
        "last month",
        "this week",
        "last week",
        "сегодня",
        "вчера",
        "на этой неделе",
        "на прошлой неделе",
        "в этом месяце",
        "в прошлом месяце",
        "q1",
        "q2",
        "q3",
        "q4",
    )
    _SNAPSHOT_RESULT_TOKENS = (
        "составляет",
        "составил",
        "равен",
        "равна",
        "вырос",
        "выросла",
        "упал",
        "упала",
        "снизил",
        "снизила",
        "увеличил",
        "увеличила",
        "лучше",
        "хуже",
        "выше",
        "ниже",
        "лидирует",
        "top ",
        "best ",
        "worst ",
        "higher",
        "lower",
        "increased",
        "decreased",
        "dropped",
        "grew",
        "was ",
        "were ",
        "equals",
        "equal to",
    )
    _SNAPSHOT_QUESTION_TOKENS = (
        "сколько",
        "какой результат",
        "какая выручка",
        "какой revenue",
        "what is the revenue",
        "what was the revenue",
        "which campaign worked better",
        "какая кампания сработала лучше",
        "кто лучше",
        "what happened",
    )

    def __init__(
        self,
        chat_repository: ChatRepository,
        llm_connector_repository: LlmConnectorRepository,
        rag_connector_repository: RagConnectorRepository,
    ):
        self._chat_repository = chat_repository
        self._llm_connector_repository = llm_connector_repository
        self._rag_connector_repository = rag_connector_repository

    def process_feedback(self, request: RagPipelineTaskRequest) -> RagPipelineResult:
        logger.info(
            "rag.pipeline.started",
            extra={
                "workspace_id": request.workspace_id,
                "conversation_id": request.conversation_id,
                "message_id": request.message_id,
                "rag_connector_name": request.rag_connector_name,
            },
        )
        message = self._chat_repository.get_message(request.conversation_id, request.message_id)
        if message is None:
            return RagPipelineResult(status="skipped", reason="message_not_found")
        if not self._hard_gate_passes(message):
            return RagPipelineResult(status="skipped", reason="hard_gate_rejected")

        conversation = self._chat_repository.get_conversation(request.conversation_id)
        if conversation is None or conversation.workspace_id != request.workspace_id:
            return RagPipelineResult(status="skipped", reason="conversation_workspace_mismatch")

        rag_connection = self._rag_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=request.rag_connector_name,
        )
        if rag_connection is None:
            return RagPipelineResult(status="skipped", reason="rag_connector_missing")

        llm_connection = self._llm_connector_repository.get_connection(workspace_id=request.workspace_id)
        if llm_connection is None:
            return RagPipelineResult(status="skipped", reason="llm_connector_missing")

        user_question = self._find_user_question(
            conversation_id=request.conversation_id,
            assistant_message_id=request.message_id,
        )
        deterministic_rejection_reason = self._deterministic_rejection_reason(
            user_question=user_question,
            message=message,
        )
        if deterministic_rejection_reason is not None:
            return RagPipelineResult(status="skipped", reason=deterministic_rejection_reason)
        decision = self._run_llm_gate(
            llm_client=LlmClient(connection=llm_connection),
            user_question=user_question,
            message=message,
        )
        logger.info(
            "rag.pipeline.llm_gate.completed",
            extra={
                "workspace_id": request.workspace_id,
                "message_id": request.message_id,
                "should_store": decision.should_store,
                "confidence": decision.confidence,
                "reason": decision.reason,
            },
        )
        if not decision.should_store:
            return RagPipelineResult(status="skipped", reason=f"llm_gate:{decision.reason}")
        if decision.confidence < rag_connection.confidence_threshold:
            return RagPipelineResult(
                status="skipped",
                reason="llm_confidence_below_threshold",
            )

        provider: EmbeddingProvider = FastEmbedProvider(rag_connection.embedding_model)
        vector = provider.embed(decision.compact_summary)
        if not vector:
            return RagPipelineResult(status="skipped", reason="embedding_failed")

        now = datetime.now(tz=UTC).isoformat()
        store = QdrantInsightsStore(rag_connection)
        similar = store.search_similar(
            workspace_id=request.workspace_id,
            vector=vector,
            limit=3,
            score_threshold=self._merge_similarity_threshold(rag_connection.extra_settings),
        )
        if similar:
            similar_record = similar[0]
            similar_id = str(similar_record.get("id"))
            existing_payload = similar_record.get("payload")
            if isinstance(existing_payload, dict):
                likes_count = self._to_int(existing_payload.get("likes_count"), default=0)
                liked_message_ids = self._liked_message_ids(existing_payload.get("liked_message_ids"))
                if request.message_id in liked_message_ids:
                    return RagPipelineResult(
                        status="stored",
                        reason="already_linked_to_existing",
                        point_id=similar_id,
                    )

                likes_count += 1
                liked_message_ids.append(request.message_id)
                merge_decision = self._run_merge_decision(
                    llm_client=LlmClient(connection=llm_connection),
                    existing_payload=existing_payload,
                    new_decision=decision,
                    message=message,
                )
                if merge_decision.should_update_existing:
                    store.update_payload(
                        point_id=similar_id,
                        payload={
                            "summary": merge_decision.updated_summary,
                            "article_markdown": self._normalize_markdown_article(
                                merge_decision.updated_article_markdown
                                or str(existing_payload.get("article_markdown") or "")
                            ),
                            "keywords": merge_decision.updated_keywords,
                            "answer_excerpt": message.content[:1200],
                            "generated_sql": message.generated_sql or existing_payload.get("generated_sql"),
                            "context_tables": self._merge_string_lists(
                                existing_payload.get("context_tables"),
                                message.context_tables,
                            ),
                            "metadata_sources": self._merge_string_lists(
                                existing_payload.get("metadata_sources"),
                                message.metadata_sources,
                            ),
                            "llm_confidence": decision.confidence,
                            "llm_reason": merge_decision.reason,
                            "likes_count": likes_count,
                            "liked_message_ids": liked_message_ids,
                            "verification_status": "pending",
                            "human_verified": False,
                            "verified_by": None,
                            "verified_at": None,
                            "updated_at": now,
                        },
                    )
                    return RagPipelineResult(
                        status="stored",
                        reason="merged_into_existing",
                        point_id=similar_id,
                    )

                store.update_payload(
                    point_id=similar_id,
                    payload={
                        "likes_count": likes_count,
                        "liked_message_ids": liked_message_ids,
                        "updated_at": now,
                    },
                )
                return RagPipelineResult(
                    status="stored",
                    reason="linked_existing_like",
                    point_id=similar_id,
                )

        point_id = self._build_point_id(request.workspace_id, request.message_id)
        payload: dict[str, object] = {
            "workspace_id": request.workspace_id,
            "conversation_id": request.conversation_id,
            "message_id": request.message_id,
            "execution_id": message.execution_id,
            "summary": decision.compact_summary,
            "article_markdown": self._normalize_markdown_article(
                decision.article_markdown
                or self._default_article_markdown(
                    summary=decision.compact_summary,
                    answer_excerpt=message.content[:1200],
                    generated_sql=message.generated_sql,
                    context_tables=message.context_tables,
                    metadata_sources=message.metadata_sources,
                )
            ),
            "answer_excerpt": message.content[:1200],
            "generated_sql": message.generated_sql,
            "context_tables": message.context_tables,
            "metadata_sources": message.metadata_sources,
            "keywords": decision.keywords,
            "llm_confidence": decision.confidence,
            "llm_reason": decision.reason,
            "likes_count": 1,
            "liked_message_ids": [request.message_id],
            "verification_status": "pending",
            "human_verified": False,
            "verified_by": None,
            "verified_at": None,
            "created_at": now,
            "updated_at": now,
        }
        store.upsert(
            point_id=point_id,
            vector=vector,
            payload=payload,
        )
        return RagPipelineResult(status="stored", reason="ok", point_id=point_id)

    def _hard_gate_passes(self, message: ChatMessageItem) -> bool:
        if message.role != "assistant":
            return False
        if not message.content.strip():
            return False
        if message.feedback != "useful":
            return False
        for warning in message.warnings:
            lowered = warning.lower()
            if any(token in lowered for token in self._BLOCKING_WARNING_TOKENS):
                return False
        return True

    def _run_llm_gate(
        self,
        llm_client: LlmClient,
        user_question: str | None,
        message: ChatMessageItem,
    ) -> RagGateDecision:
        prompt_payload = {
            "question": user_question,
            "answer": message.content[:3500],
            "generated_sql": message.generated_sql,
            "warnings": message.warnings,
            "context_tables": message.context_tables,
            "metadata_sources": message.metadata_sources,
        }
        response = llm_client.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You evaluate if a chat answer is reusable as an analytics insight.\n"
                        "Respond with strict JSON only.\n"
                        "Schema:\n"
                        "{"
                        '"should_store": boolean, '
                        '"confidence": number(0..1), '
                        '"reason": string, '
                        '"compact_summary": string, '
                        '"keywords": string[], '
                        '"article_markdown": string'
                        "}\n"
                        "Store only durable, reusable analytics knowledge.\n"
                        "Good candidates: metric/term definitions, table or column purpose, source-of-truth mapping, "
                        "lineage or join guidance, where-to-find-data guidance, caveats, and reusable analysis playbooks.\n"
                        "Reject point-in-time facts or one-off results, including month/week/day-specific KPI values, "
                        "single query outcomes, rankings, or statements like 'for March net_revenue was X'.\n"
                        "article_markdown must be concise, clean markdown for reusable analytics knowledge.\n"
                        "Use sections and bullets, add SQL code block only when relevant.\n"
                        "Reject if answer is low-quality, unsafe, ambiguous, or lacks durable analytical value."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False),
                },
            ]
        )

        content = self._extract_content(response.message.get("content"))
        gate_payload = self._parse_json_payload(content)
        if gate_payload is None:
            return self._fallback_gate_decision(
                reason="invalid_llm_json",
                user_question=user_question,
                message=message,
            )
        try:
            decision = RagGateDecision.model_validate(gate_payload)
            if not decision.article_markdown or not decision.article_markdown.strip():
                decision.article_markdown = self._default_article_markdown(
                    summary=decision.compact_summary,
                    answer_excerpt=message.content[:1200],
                    generated_sql=message.generated_sql,
                    context_tables=message.context_tables,
                    metadata_sources=message.metadata_sources,
                )
            decision.article_markdown = self._normalize_markdown_article(decision.article_markdown)
        except ValidationError:
            return self._fallback_gate_decision(
                reason="invalid_llm_schema",
                user_question=user_question,
                message=message,
            )
        else:
            return decision

    def _run_merge_decision(
        self,
        llm_client: LlmClient,
        existing_payload: dict[str, object],
        new_decision: RagGateDecision,
        message: ChatMessageItem,
    ) -> RagMergeDecision:
        prompt_payload = {
            "existing_summary": existing_payload.get("summary"),
            "existing_article_markdown": existing_payload.get("article_markdown"),
            "existing_keywords": existing_payload.get("keywords"),
            "existing_answer_excerpt": existing_payload.get("answer_excerpt"),
            "new_summary": new_decision.compact_summary,
            "new_article_markdown": new_decision.article_markdown,
            "new_keywords": new_decision.keywords,
            "new_answer_excerpt": message.content[:1200],
            "new_generated_sql": message.generated_sql,
        }
        response = llm_client.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You decide whether the new liked chat answer adds meaningful new information "
                        "to an existing insight document.\n"
                        "Respond with strict JSON only.\n"
                        "Schema:\n"
                        "{"
                        '"should_update_existing": boolean, '
                        '"reason": string, '
                        '"updated_summary": string, '
                        '"updated_keywords": string[], '
                        '"updated_article_markdown": string'
                        "}\n"
                        "If no meaningful update is needed, return should_update_existing=false and "
                        "keep updated_summary close to existing summary.\n"
                        "updated_article_markdown must stay concise and readable markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False),
                },
            ]
        )
        content = self._extract_content(response.message.get("content"))
        payload = self._parse_json_payload(content)
        if payload is None:
            return RagMergeDecision(
                should_update_existing=False,
                reason="invalid_merge_json",
                updated_summary=str(existing_payload.get("summary") or new_decision.compact_summary),
                updated_keywords=new_decision.keywords,
                updated_article_markdown=self._normalize_markdown_article(
                    str(existing_payload.get("article_markdown") or new_decision.article_markdown or "")
                ),
            )
        try:
            decision = RagMergeDecision.model_validate(payload)
            if not decision.updated_article_markdown or not decision.updated_article_markdown.strip():
                decision.updated_article_markdown = str(
                    existing_payload.get("article_markdown") or new_decision.article_markdown or ""
                )
            decision.updated_article_markdown = self._normalize_markdown_article(
                decision.updated_article_markdown
            )
        except ValidationError:
            return RagMergeDecision(
                should_update_existing=False,
                reason="invalid_merge_schema",
                updated_summary=str(existing_payload.get("summary") or new_decision.compact_summary),
                updated_keywords=new_decision.keywords,
                updated_article_markdown=self._normalize_markdown_article(
                    str(existing_payload.get("article_markdown") or new_decision.article_markdown or "")
                ),
            )
        else:
            return decision

    @staticmethod
    def _extract_content(content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunks).strip()
        return ""

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, object] | None:
        if not content:
            return None
        normalized = content.strip()
        if normalized.startswith("```") and "```" in normalized[3:]:
            lines = normalized.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            normalized = "\n".join(lines).strip()

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start < 0 or end <= start:
                return None
            candidate = normalized[start : end + 1]
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, dict) else None

    def _find_user_question(self, conversation_id: str, assistant_message_id: str) -> str | None:
        messages = self._chat_repository.get_messages(conversation_id=conversation_id, limit=200)
        for index, message in enumerate(messages):
            if message.id != assistant_message_id:
                continue
            for previous in reversed(messages[:index]):
                if previous.role == "user" and previous.content.strip():
                    return previous.content
            return None
        return None

    @staticmethod
    def _build_point_id(workspace_id: str, message_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"{workspace_id}:{message_id}"))

    @staticmethod
    def _merge_similarity_threshold(extra_settings: dict[str, object]) -> float:
        raw = extra_settings.get("merge_similarity_threshold")
        if isinstance(raw, float | int):
            value = float(raw)
            if 0 <= value <= 1:
                return value
        return RagInsightPipelineService._DEFAULT_MERGE_SIMILARITY_THRESHOLD

    @staticmethod
    def _liked_message_ids(raw: object) -> list[str]:
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        for item in raw:
            if isinstance(item, str) and item not in result:
                result.append(item)
        return result

    @staticmethod
    def _merge_string_lists(left: object, right: list[str]) -> list[str]:
        merged: list[str] = []
        if isinstance(left, list):
            for item in left:
                if isinstance(item, str) and item not in merged:
                    merged.append(item)
        for item in right:
            if item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _to_int(value: object, default: int) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return default

    @staticmethod
    def _default_article_markdown(
        summary: str,
        answer_excerpt: str,
        generated_sql: str | None,
        context_tables: list[str],
        metadata_sources: list[str],
    ) -> str:
        table_lines = "\n".join(f"- `{table}`" for table in context_tables[:10])
        source_lines = ", ".join(metadata_sources[:5]) or "unknown"
        sql_block = f"\n## SQL\n```sql\n{generated_sql}\n```\n" if generated_sql else ""
        return (
            f"# Insight\n\n"
            f"## Summary\n{summary.strip()}\n\n"
            f"## Key Details\n{answer_excerpt.strip()}\n"
            f"{sql_block}"
            f"## Context Tables\n{table_lines or '- none'}\n\n"
            f"## Metadata Sources\n{source_lines}\n"
        )

    @staticmethod
    def _normalize_markdown_article(value: str) -> str:
        cleaned = value.replace("\r\n", "\n").strip()
        while "\n\n\n" in cleaned:
            cleaned = cleaned.replace("\n\n\n", "\n\n")
        if not cleaned.startswith("#"):
            cleaned = f"# Insight\n\n{cleaned}"
        return cleaned[:12000]

    def _fallback_gate_decision(
        self,
        reason: str,
        user_question: str | None,
        message: ChatMessageItem,
    ) -> RagGateDecision:
        should_store = self._deterministic_rejection_reason(
            user_question=user_question,
            message=message,
        ) is None and not self._is_trivial_smalltalk(
            answer=message.content,
            user_question=user_question,
            generated_sql=message.generated_sql,
            context_tables=message.context_tables,
        )
        compact_summary = message.content.strip()[:300] or "Useful assistant answer"
        return RagGateDecision(
            should_store=should_store,
            confidence=0.72 if should_store else 0.0,
            reason=f"{reason}_fallback",
            compact_summary=compact_summary,
            keywords=self._fallback_keywords(
                user_question=user_question,
                context_tables=message.context_tables,
            ),
            article_markdown=self._default_article_markdown(
                summary=compact_summary,
                answer_excerpt=message.content[:1200],
                generated_sql=message.generated_sql,
                context_tables=message.context_tables,
                metadata_sources=message.metadata_sources,
            ),
        )

    @staticmethod
    def _contains_any_token(text: str, tokens: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in tokens)

    @classmethod
    def _has_reusable_knowledge_signal(cls, user_question: str | None, answer: str) -> bool:
        combined = f"{user_question or ''}\n{answer}"
        return cls._contains_any_token(combined, cls._REUSABLE_KNOWLEDGE_TOKENS)

    @staticmethod
    def _has_numeric_fact_signal(text: str) -> bool:
        return re.search(r"\b\d+(?:[.,]\d+)?(?:\s?(?:%|pp|x|k|m|b|тыс|млн|млрд|₽|\$|€))?\b", text.lower()) is not None

    @classmethod
    def _looks_like_single_use_fact(cls, user_question: str | None, answer: str) -> bool:
        normalized_answer = answer.strip().lower()
        normalized_question = (user_question or "").strip().lower()
        has_temporal_signal = cls._contains_any_token(normalized_answer, cls._TEMPORAL_TOKENS) or cls._contains_any_token(
            normalized_question,
            cls._TEMPORAL_TOKENS,
        )
        has_result_signal = cls._contains_any_token(normalized_answer, cls._SNAPSHOT_RESULT_TOKENS)
        has_snapshot_question = cls._contains_any_token(normalized_question, cls._SNAPSHOT_QUESTION_TOKENS)
        has_numeric_signal = cls._has_numeric_fact_signal(normalized_answer)
        return has_numeric_signal and (has_temporal_signal or has_result_signal or has_snapshot_question)

    @classmethod
    def _deterministic_rejection_reason(
        cls,
        user_question: str | None,
        message: ChatMessageItem,
    ) -> str | None:
        if cls._has_reusable_knowledge_signal(user_question=user_question, answer=message.content):
            return None
        if cls._looks_like_single_use_fact(user_question=user_question, answer=message.content):
            return "deterministic_gate_ephemeral_fact"
        return None

    @staticmethod
    def _is_trivial_smalltalk(
        answer: str,
        user_question: str | None,
        generated_sql: str | None,
        context_tables: list[str],
    ) -> bool:
        if generated_sql or context_tables:
            return False
        normalized_answer = answer.strip().lower()
        if len(normalized_answer) >= 140:
            return False
        text = f"{(user_question or '').lower()} {normalized_answer}"
        smalltalk_tokens = (
            "привет",
            "hello",
            "hi ",
            "чем могу помочь",
            "how can i help",
            "чем помочь",
            "готов помочь",
            "i can help",
        )
        return any(token in text for token in smalltalk_tokens)

    @staticmethod
    def _fallback_keywords(user_question: str | None, context_tables: list[str]) -> list[str]:
        keywords: list[str] = []
        for table in context_tables:
            table_name = table.rsplit(".", maxsplit=1)[-1].strip().lower()
            if table_name and table_name not in keywords:
                keywords.append(table_name)
        if user_question:
            for token in re.findall(r"[0-9A-Za-z_]{4,}", user_question.lower()):
                if token not in keywords:
                    keywords.append(token)
                if len(keywords) >= 12:
                    break
        return keywords[:12]
