from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import Timer
from typing import Any, Literal

from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    MattermostConnectorItem,
)
from rhetorica_backend.contracts.chat import (
    ChatActorContext,
    ChatAskRequest,
    ChatAskResponse,
)
from rhetorica_backend.domain.chat.llm.llm_client import LlmClient
from rhetorica_backend.domain.chat.orchestration.final_payload_policy import FinalPayloadPolicy
from rhetorica_backend.domain.exports.service import AnalysisExportService
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.mattermost import MattermostClient, MattermostPost
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)

logger = get_logger(__name__)
DEFAULT_MATTERMOST_ACK_EMOJI_NAME = "eyes"
DEFAULT_MATTERMOST_ACK_DELAY_SECONDS = 5.0
_AckScheduler = Callable[[float, Callable[[], None]], Callable[[], None]]


@dataclass(frozen=True)
class MattermostPostEvent:
    post_id: str
    channel_id: str
    user_id: str
    message: str
    channel_type: str
    post_type: str = ""
    root_id: str | None = None
    parent_id: str | None = None
    sender_name: str | None = None
    mentions: list[str] | None = None

    @property
    def thread_root_id(self) -> str:
        return self.root_id or self.parent_id or self.post_id


@dataclass(frozen=True)
class MattermostHandleResult:
    responded: bool
    conversation_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _MattermostQuestion:
    text: str
    thread_key: str
    root_id: str | None
    trigger: Literal["explicit", "ambient"] = "explicit"


@dataclass(frozen=True)
class MattermostConversationContext:
    markdown: str | None
    diagnostics: MattermostContextDiagnostics


@dataclass(frozen=True)
class MattermostContextDiagnostics:
    fetched: int = 0
    excluded_current: int = 0
    excluded_bot: int = 0
    excluded_history: int = 0
    retained: int = 0
    truncated: bool = False
    character_count: int = 0


@dataclass(frozen=True)
class MattermostAmbientDecisionRequest:
    connector_name: str
    decision_prompt: str
    event: MattermostPostEvent
    context_markdown: str | None


@dataclass(frozen=True)
class MattermostAmbientDecision:
    should_respond: bool
    question: str
    reason: str


class MattermostResponsePolicy:
    """Decides when a Mattermost post should enter the analytics chat runtime."""

    _DIRECT_CHANNEL_TYPES = frozenset({"D"})
    _BROADCAST_MENTION_PATTERN = re.compile(
        r"(^|\s)@(all|channel|here)(?=\b|[:,.!?])",
        re.IGNORECASE,
    )

    def should_respond(
        self,
        *,
        event: MattermostPostEvent,
        bot_user_id: str,
        bot_username: str,
        reply_to_bot_post: bool = False,
        ambient_listen_enabled: bool = False,
        ambient_channel_ids: list[str] | None = None,
    ) -> _MattermostQuestion | None:
        message = event.message.strip()
        if not message:
            return None
        if event.post_type.startswith("system_"):
            return None
        if event.user_id == bot_user_id:
            return None

        if event.channel_type in self._DIRECT_CHANNEL_TYPES:
            return _MattermostQuestion(
                text=message,
                thread_key=event.channel_id,
                root_id=event.root_id,
            )

        mention_pattern = re.compile(rf"(^|\s)@{re.escape(bot_username)}(?=\b|[:,.!?])")
        mentioned_by_id = (
            bot_user_id in (event.mentions or [])
            and not self._has_broadcast_mention(message)
        )
        mentioned_by_name = bool(mention_pattern.search(message))
        if not mentioned_by_id and not mentioned_by_name and not reply_to_bot_post:
            if self._should_consider_ambient(
                event=event,
                ambient_listen_enabled=ambient_listen_enabled,
                ambient_channel_ids=ambient_channel_ids or [],
            ):
                return _MattermostQuestion(
                    text=message,
                    thread_key=event.thread_root_id,
                    root_id=event.thread_root_id,
                    trigger="ambient",
                )
            return None

        cleaned = mention_pattern.sub(" ", message).strip()
        return _MattermostQuestion(
            text=cleaned if mentioned_by_name else message,
            thread_key=event.thread_root_id,
            root_id=event.thread_root_id,
        )

    def _has_broadcast_mention(self, message: str) -> bool:
        return bool(self._BROADCAST_MENTION_PATTERN.search(message))

    @staticmethod
    def _should_consider_ambient(
        *,
        event: MattermostPostEvent,
        ambient_listen_enabled: bool,
        ambient_channel_ids: list[str],
    ) -> bool:
        if not ambient_listen_enabled:
            return False
        if event.channel_type not in {"O", "P"}:
            return False
        return event.channel_id in set(ambient_channel_ids)


class MattermostAmbientDecisionService:
    """Asks the configured LLM whether an unmentioned Mattermost post needs the bot."""

    def __init__(
        self,
        *,
        llm_connection: LlmConnectionConfig,
        llm_client_factory: Callable[[LlmConnectionConfig], LlmClient] = LlmClient,
    ) -> None:
        self._llm_connection = llm_connection
        self._llm_client_factory = llm_client_factory

    def decide(self, request: MattermostAmbientDecisionRequest) -> MattermostAmbientDecision:
        client = self._llm_client_factory(self._llm_connection)
        response, _tools_disabled, _structured_disabled = client.chat_completion_with_fallback(
            messages=[
                {"role": "system", "content": request.decision_prompt},
                {
                    "role": "user",
                    "content": self._decision_payload(request),
                },
            ],
            tools=[],
            response_schema=_ambient_decision_json_schema(),
        )
        content = response.message.get("content")
        if not isinstance(content, str):
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason="mattermost_ambient_decision_missing_content",
            )
        try:
            parsed = _parse_ambient_decision_payload(content)
        except ValueError as exc:
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason=str(exc),
            )
        if not isinstance(parsed, dict):
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason="mattermost_ambient_decision_invalid_payload",
            )
        should_respond = parsed.get("should_respond")
        question = parsed.get("question")
        reason = parsed.get("reason")
        if not isinstance(should_respond, bool):
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason="mattermost_ambient_decision_missing_should_respond",
            )
        if question is not None and not isinstance(question, str):
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason="mattermost_ambient_decision_invalid_question",
            )
        if reason is not None and not isinstance(reason, str):
            return MattermostAmbientDecision(
                should_respond=False,
                question=request.event.message.strip(),
                reason="mattermost_ambient_decision_invalid_reason",
            )
        return MattermostAmbientDecision(
            should_respond=should_respond,
            question=(question or request.event.message).strip(),
            reason=(reason or "").strip(),
        )

    @staticmethod
    def _decision_payload(request: MattermostAmbientDecisionRequest) -> str:
        payload = {
            "connector_name": request.connector_name,
            "post": {
                "id": request.event.post_id,
                "channel_id": request.event.channel_id,
                "user_id": request.event.user_id,
                "message": request.event.message,
                "root_id": request.event.root_id,
                "parent_id": request.event.parent_id,
                "sender_name": request.event.sender_name,
            },
            "discussion_context_markdown": request.context_markdown,
        }
        return json.dumps(payload, ensure_ascii=False)


def _ambient_decision_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "should_respond": {"type": "boolean"},
            "question": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["should_respond", "question", "reason"],
    }


def _parse_ambient_decision_payload(raw_content: str) -> dict[str, Any]:
    cleaned = FinalPayloadPolicy.strip_thinking_tags(raw_content).strip()
    for candidate in _json_object_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("mattermost_ambient_decision_invalid_json")


def _json_object_candidates(content: str) -> list[str]:
    candidates: list[str] = []
    for start, char in enumerate(content):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            current = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(content[start : index + 1])
                    break
    return candidates


class MattermostResponseRenderer:
    """Formats Rhetorica chat responses for Mattermost posts."""

    _CHANNEL_LINK_TILDE_PATTERN = re.compile(r"(?<![\\~])~(?![~\s])")

    def render(self, response: ChatAskResponse, *, send_sql: bool = False) -> str:
        answer = self._CHANNEL_LINK_TILDE_PATTERN.sub(r"\\~", response.answer.strip())
        generated_sql = response.generated_sql
        parts = [answer.strip() or "Ответ пустой. Попробуй уточнить вопрос."]
        if send_sql and generated_sql:
            parts.append(f"```sql\n{generated_sql.strip()}\n```")
        return "\n\n".join(parts)


class MattermostMessageSplitter:
    """Splits rendered responses into Mattermost-safe post bodies."""

    MAX_POST_CHARACTERS = 16_000

    def split(self, message: str) -> list[str]:
        remaining = message.strip()
        if not remaining:
            return [""]

        chunks: list[str] = []
        while len(remaining) > self.MAX_POST_CHARACTERS:
            split_at = self._preferred_split_index(remaining)
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _preferred_split_index(self, message: str) -> int:
        limit = self.MAX_POST_CHARACTERS
        for separator in ("\n\n", "\n", " "):
            split_at = message.rfind(separator, 0, limit)
            if split_at > 0:
                return split_at + len(separator)
        return limit


class MattermostContextBuilder:
    """Builds bounded nearby Mattermost context for a chat turn."""

    _MAX_THREAD_POSTS = 30
    _MAX_CHANNEL_CONTEXT_POSTS = 12
    _MAX_CONTEXT_CHARS = 8_000

    def build(
        self,
        *,
        client: MattermostClient,
        event: MattermostPostEvent,
        bot_user_id: str,
        force_thread_context: bool = False,
        excluded_post_ids: set[str] | None = None,
    ) -> MattermostConversationContext:
        if force_thread_context or event.root_id or event.parent_id:
            posts = self._thread_posts(client=client, event=event)
            title = "Mattermost thread context:"
        else:
            posts = self._channel_nearby_posts(client=client, event=event)
            title = "Mattermost nearby channel context:"
        markdown, diagnostics = self._format_posts(
            client=client,
            posts=posts,
            title=title,
            bot_user_id=bot_user_id,
            current_post_id=event.post_id,
            excluded_post_ids=excluded_post_ids or set(),
            author_overrides={event.user_id: event.sender_name} if event.sender_name else None,
        )
        return MattermostConversationContext(
            markdown=markdown,
            diagnostics=diagnostics,
        )

    def _thread_posts(
        self,
        *,
        client: MattermostClient,
        event: MattermostPostEvent,
    ) -> list[MattermostPost]:
        try:
            posts = client.get_post_thread(event.thread_root_id)
        except Exception:
            logger.debug("mattermost.context.thread_fetch_failed", root_id=event.thread_root_id)
            posts = []
        return self._with_current_post(
            posts=posts,
            event=event,
            max_posts=self._MAX_THREAD_POSTS,
        )

    def _channel_nearby_posts(
        self,
        *,
        client: MattermostClient,
        event: MattermostPostEvent,
    ) -> list[MattermostPost]:
        try:
            previous = client.get_channel_posts_before(
                channel_id=event.channel_id,
                post_id=event.post_id,
                per_page=self._MAX_CHANNEL_CONTEXT_POSTS,
            )
        except Exception:
            logger.debug(
                "mattermost.context.channel_fetch_failed",
                channel_id=event.channel_id,
                post_id=event.post_id,
            )
            previous = []
        current = MattermostPost(
            id=event.post_id,
            channel_id=event.channel_id,
            user_id=event.user_id,
            message=event.message,
            root_id=event.root_id,
            parent_id=event.parent_id,
        )
        return [
            *self._sort_posts(previous)[-self._MAX_CHANNEL_CONTEXT_POSTS :],
            current,
        ]

    def _with_current_post(
        self,
        *,
        posts: list[MattermostPost],
        event: MattermostPostEvent,
        max_posts: int,
    ) -> list[MattermostPost]:
        previous = [
            post
            for post in self._sort_posts(posts)
            if post.id != event.post_id
        ]
        current = MattermostPost(
            id=event.post_id,
            channel_id=event.channel_id,
            user_id=event.user_id,
            message=event.message,
            root_id=event.root_id,
            parent_id=event.parent_id,
        )
        if max_posts <= 1:
            return [current]
        return [*previous[-(max_posts - 1) :], current]

    def _format_posts(
        self,
        *,
        client: MattermostClient,
        posts: list[MattermostPost],
        title: str,
        bot_user_id: str,
        current_post_id: str,
        excluded_post_ids: set[str],
        author_overrides: dict[str, str] | None = None,
    ) -> tuple[str | None, MattermostContextDiagnostics]:
        _ = title
        blocks: list[str] = []
        author_cache: dict[str, str] = dict(author_overrides or {})
        excluded_current = 0
        excluded_bot = 0
        excluded_history = 0
        for post in posts:
            if post.id == current_post_id:
                excluded_current += 1
                continue
            if post.user_id == bot_user_id:
                excluded_bot += 1
                continue
            if post.id in excluded_post_ids:
                excluded_history += 1
                continue
            message = self._clean_post_message(post.message)
            if not post.id or not message:
                continue
            author = self._author_label(
                client=client,
                post=post,
                bot_user_id=bot_user_id,
                author_cache=author_cache,
            )
            blocks.append(f"- {author}: {message}")
        markdown, truncated, retained = self._bounded_recent_blocks(blocks)
        diagnostics = MattermostContextDiagnostics(
            fetched=len(posts),
            excluded_current=excluded_current,
            excluded_bot=excluded_bot,
            excluded_history=excluded_history,
            retained=retained,
            truncated=truncated,
            character_count=len(markdown or ""),
        )
        return markdown, diagnostics

    @classmethod
    def _bounded_recent_blocks(
        cls,
        blocks: list[str],
    ) -> tuple[str | None, bool, int]:
        if not blocks:
            return None, False, 0
        selected_reversed: list[str] = []
        used = 0
        truncated = False
        marker = "\n[... older message truncated ...]"
        for block in reversed(blocks):
            separator_chars = 1 if selected_reversed else 0
            remaining = cls._MAX_CONTEXT_CHARS - used - separator_chars
            if remaining <= 0:
                truncated = True
                break
            if len(block) > remaining:
                truncated = True
                if remaining > len(marker):
                    block = f"{block[: remaining - len(marker)]}{marker}"
                    selected_reversed.append(block)
                break
            selected_reversed.append(block)
            used += len(block) + separator_chars
        selected = list(reversed(selected_reversed))
        if len(selected) < len(blocks):
            truncated = True
        return "\n".join(selected) or None, truncated, len(selected)

    @staticmethod
    def _sort_posts(posts: list[MattermostPost]) -> list[MattermostPost]:
        return sorted(posts, key=lambda post: (post.create_at, post.id))

    @staticmethod
    def _clean_post_message(message: str) -> str:
        normalized = message.strip()
        return normalized

    @staticmethod
    def _author_label(
        *,
        client: MattermostClient,
        post: MattermostPost,
        bot_user_id: str,
        author_cache: dict[str, str],
    ) -> str:
        if post.user_id == bot_user_id:
            return "Бот"
        cached = author_cache.get(post.user_id)
        if cached is not None:
            return cached
        try:
            user = client.get_user(post.user_id)
        except Exception:
            logger.debug("mattermost.context.user_fetch_failed", user_id=post.user_id)
            label = post.user_id
        else:
            label = user.display_name or user.username
        author_cache[post.user_id] = label
        return label


class MattermostTransportService:
    """Routes Mattermost posts into the existing Rhetorica chat runtime."""

    def __init__(
        self,
        *,
        chat_repository: ChatRepository,
        mattermost_repository: MattermostConnectorRepository,
        ask_chat: Callable[[ChatAskRequest], ChatAskResponse],
        mattermost_client_factory: Callable[[MattermostConnectorItem], MattermostClient],
        export_service: AnalysisExportService | None = None,
        response_policy: MattermostResponsePolicy | None = None,
        response_renderer: MattermostResponseRenderer | None = None,
        message_splitter: MattermostMessageSplitter | None = None,
        context_builder: MattermostContextBuilder | None = None,
        ambient_decision_service: MattermostAmbientDecisionService | None = None,
        ack_delay_seconds: float = DEFAULT_MATTERMOST_ACK_DELAY_SECONDS,
        ack_scheduler: _AckScheduler | None = None,
    ) -> None:
        self._chat_repository = chat_repository
        self._mattermost_repository = mattermost_repository
        self._ask_chat = ask_chat
        self._mattermost_client_factory = mattermost_client_factory
        self._export_service = export_service or AnalysisExportService()
        self._response_policy = response_policy or MattermostResponsePolicy()
        self._response_renderer = response_renderer or MattermostResponseRenderer()
        self._message_splitter = message_splitter or MattermostMessageSplitter()
        self._context_builder = context_builder or MattermostContextBuilder()
        self._ambient_decision_service = ambient_decision_service
        self._ack_delay_seconds = ack_delay_seconds
        self._ack_scheduler = ack_scheduler or _schedule_timer

    def handle_post(
        self,
        *,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
    ) -> MattermostHandleResult:
        client = self._mattermost_client_factory(connector)
        bot_user = client.get_me()
        bot_username = connector.connection.bot_username or bot_user.username
        reply_to_bot_post = self._is_reply_to_bot_post(
            client=client,
            event=event,
            bot_user_id=bot_user.id,
        )
        question = self._response_policy.should_respond(
            event=event,
            bot_user_id=bot_user.id,
            bot_username=bot_username,
            reply_to_bot_post=reply_to_bot_post,
            ambient_listen_enabled=connector.connection.ambient_listen_enabled,
            ambient_channel_ids=connector.connection.ambient_channel_ids,
        )
        if question is None:
            logger.debug(
                "mattermost.post.ignored",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
            )
            return MattermostHandleResult(responded=False, reason="ignored_by_policy")

        if not self._mattermost_repository.claim_event(
            workspace_id=connector.workspace_id,
            connector_id=connector.id,
            channel_id=event.channel_id,
            post_id=event.post_id,
            trigger=question.trigger,
        ):
            logger.info(
                "mattermost.post.duplicate_ignored",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
            )
            return MattermostHandleResult(responded=False, reason="duplicate_event")

        logger.info(
            "mattermost.post.accepted",
            connector_id=connector.id,
            workspace_id=connector.workspace_id,
            channel_id=event.channel_id,
            post_id=event.post_id,
            trigger=question.trigger,
        )
        if question.trigger == "ambient":
            ambient_context = self._context_builder.build(
                client=client,
                event=event,
                bot_user_id=bot_user.id,
                force_thread_context=True,
            )
            question = self._decide_ambient_question(
                connector=connector,
                event=event,
                question=question,
                ambient_context=ambient_context,
            )
            if question is None:
                return MattermostHandleResult(responded=False, reason="ambient_decision_ignored")

        root_id = question.root_id or event.root_id
        cancel_ack = (
            _noop_cancel
            if question.trigger == "ambient"
            else self._schedule_ack(
                client=client,
                user_id=bot_user.id,
                post_id=event.post_id,
            )
        )
        try:
            conversation_id = self._mattermost_repository.get_or_create_binding(
                workspace_id=connector.workspace_id,
                connector_id=connector.id,
                channel_id=event.channel_id,
                thread_key=question.thread_key,
                conversation_id_factory=lambda: self._create_conversation(connector, event),
                conversation_exists=self._conversation_exists,
            )
            excluded_post_ids = self._persisted_mattermost_post_ids(
                connector_id=connector.id,
                conversation_id=conversation_id,
            )
            ambient_context = self._context_builder.build(
                client=client,
                event=event,
                bot_user_id=bot_user.id,
                force_thread_context=question.trigger == "ambient",
                excluded_post_ids=excluded_post_ids,
            )
            logger.info(
                "mattermost.context.assembled",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                conversation_id=conversation_id,
                **ambient_context.diagnostics.__dict__,
            )
            response = self._ask_chat(
                ChatAskRequest(
                    workspace_id=connector.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=connector.agent_id,
                    question=question.text,
                    run_sql=True,
                    ambient_context_markdown=ambient_context.markdown,
                    source_provider="mattermost",
                    source_message_id=event.post_id,
                    actor_context=self._build_actor_context(
                        client=client,
                        connector=connector,
                        event=event,
                    ),
                )
            )
            file_ids = self._render_response_files(
                client=client,
                channel_id=event.channel_id,
                question=question.text,
                response=response,
                workspace_id=connector.workspace_id,
            )
            cancel_ack()
            rendered_message = self._response_renderer.render(
                response,
                send_sql=connector.connection.send_sql_enabled,
            )
            post_messages = self._message_splitter.split(rendered_message)
            for index, post_message in enumerate(post_messages):
                client.create_post(
                    channel_id=event.channel_id,
                    message=post_message,
                    root_id=root_id,
                    file_ids=(file_ids or None) if index == 0 else None,
                )
            logger.info(
                "mattermost.post.responded",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
                conversation_id=response.conversation_id or conversation_id,
                file_count=len(file_ids),
                post_count=len(post_messages),
            )
            self._mattermost_repository.complete_event(
                connector_id=connector.id,
                post_id=event.post_id,
                status="responded",
                conversation_id=response.conversation_id or conversation_id,
            )
            return MattermostHandleResult(
                responded=True,
                conversation_id=response.conversation_id or conversation_id,
            )
        except Exception as exc:
            self._mattermost_repository.complete_event(
                connector_id=connector.id,
                post_id=event.post_id,
                status="failed",
                reason=str(exc)[:1000],
            )
            raise
        finally:
            cancel_ack()

    def _persisted_mattermost_post_ids(
        self,
        *,
        connector_id: str,
        conversation_id: str,
    ) -> set[str]:
        source_ids: set[str] = set()
        list_source_ids = getattr(self._chat_repository, "list_source_message_ids", None)
        if callable(list_source_ids):
            source_ids.update(
                list_source_ids(
                    conversation_id=conversation_id,
                    source_provider="mattermost",
                )
            )
        list_ledger_ids = getattr(
            self._mattermost_repository,
            "list_conversation_post_ids",
            None,
        )
        if callable(list_ledger_ids):
            source_ids.update(
                list_ledger_ids(
                    connector_id=connector_id,
                    conversation_id=conversation_id,
                )
            )
        return source_ids

    def _decide_ambient_question(
        self,
        *,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
        question: _MattermostQuestion,
        ambient_context: MattermostConversationContext,
    ) -> _MattermostQuestion | None:
        prompt = connector.connection.ambient_decision_prompt
        if self._ambient_decision_service is None or not prompt:
            self._mattermost_repository.complete_event(
                connector_id=connector.id,
                post_id=event.post_id,
                status="failed",
                reason="ambient_decision_unconfigured",
            )
            logger.warning(
                "mattermost.ambient_decision.unconfigured",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
            )
            return None
        try:
            decision = self._ambient_decision_service.decide(
                MattermostAmbientDecisionRequest(
                    connector_name=connector.name,
                    decision_prompt=prompt,
                    event=event,
                    context_markdown=ambient_context.markdown,
                )
            )
        except Exception as exc:
            self._mattermost_repository.complete_event(
                connector_id=connector.id,
                post_id=event.post_id,
                status="failed",
                reason=str(exc)[:1000],
            )
            logger.exception(
                "mattermost.ambient_decision.failed",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
            )
            return None
        if not decision.should_respond:
            self._mattermost_repository.complete_event(
                connector_id=connector.id,
                post_id=event.post_id,
                status="ignored",
                reason=decision.reason[:1000],
            )
            logger.info(
                "mattermost.ambient_decision.ignored",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
                channel_id=event.channel_id,
                post_id=event.post_id,
                reason=decision.reason,
            )
            return None
        return _MattermostQuestion(
            text=question.text,
            thread_key=question.thread_key,
            root_id=question.root_id,
            trigger=question.trigger,
        )

    @staticmethod
    def _is_reply_to_bot_post(
        *,
        client: MattermostClient,
        event: MattermostPostEvent,
        bot_user_id: str,
    ) -> bool:
        if event.channel_type in MattermostResponsePolicy._DIRECT_CHANNEL_TYPES:
            return False
        # root_id only means that the post belongs to a thread. It does not mean
        # that the user replied to the root post.
        reference_id = event.parent_id
        if not reference_id:
            return False
        try:
            post = client.get_post(reference_id)
        except Exception:
            logger.debug("mattermost.reply_reference_fetch_failed", post_id=reference_id)
            return False
        return post.user_id == bot_user_id

    def _create_conversation(
        self,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
    ) -> str:
        title = f"Tag {event.channel_id}"
        if event.sender_name:
            title = f"Tag {event.sender_name}"
        conversation = self._chat_repository.create_conversation(
            workspace_id=connector.workspace_id,
            title=title,
            agent_id=connector.agent_id,
        )
        return conversation.id

    def _schedule_ack(
        self,
        *,
        client: MattermostClient,
        user_id: str,
        post_id: str,
    ) -> Callable[[], None]:
        return self._ack_scheduler(
            self._ack_delay_seconds,
            lambda: self._add_ack_reaction(
                client=client,
                user_id=user_id,
                post_id=post_id,
            ),
        )

    @staticmethod
    def _add_ack_reaction(
        *,
        client: MattermostClient,
        user_id: str,
        post_id: str,
    ) -> None:
        try:
            client.create_reaction(
                user_id=user_id,
                post_id=post_id,
                emoji_name=DEFAULT_MATTERMOST_ACK_EMOJI_NAME,
            )
        except Exception:
            logger.debug("mattermost.ack_reaction_failed", post_id=post_id)

    def _conversation_exists(self, conversation_id: str) -> bool:
        return self._chat_repository.get_conversation(conversation_id) is not None

    @staticmethod
    def _build_actor_context(
        *,
        client: MattermostClient,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
    ) -> ChatActorContext:
        login = event.sender_name
        display_name = event.sender_name
        if not login:
            try:
                user = client.get_user(event.user_id)
            except Exception:
                logger.debug("mattermost.actor_context.user_fetch_failed", user_id=event.user_id)
            else:
                login = user.username
                display_name = user.display_name or user.username
        return ChatActorContext(
            source="mattermost",
            login=login,
            display_name=display_name,
            auth_provider="mattermost",
            workspace_id=connector.workspace_id,
            external_user_id=event.user_id,
        )

    def _render_response_files(
        self,
        *,
        client: MattermostClient,
        channel_id: str,
        question: str,
        response: ChatAskResponse,
        workspace_id: str,
    ) -> list[str]:
        chart_pngs = self._export_service.render_chat_response_chart_pngs(
            response=response,
            workspace_id=workspace_id,
            question=question,
        )
        if not chart_pngs:
            return []
        file_ids: list[str] = []
        for index, chart_png in enumerate(chart_pngs, start=1):
            file_ids.append(
                client.upload_bytes(
                    channel_id=channel_id,
                    content=chart_png,
                    filename=f"rhetorica-chart-{index}.png",
                    content_type="image/png",
                )
            )
        return file_ids


def _schedule_timer(delay_seconds: float, callback: Callable[[], None]) -> Callable[[], None]:
    timer = Timer(delay_seconds, callback)
    timer.daemon = True
    timer.start()
    return timer.cancel


def _noop_cancel() -> None:
    return None
