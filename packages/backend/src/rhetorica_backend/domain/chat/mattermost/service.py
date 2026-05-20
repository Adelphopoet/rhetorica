from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from rhetorica_backend.contracts.admin_integrations import MattermostConnectorItem
from rhetorica_backend.contracts.chat import ChatAskRequest, ChatAskResponse
from rhetorica_backend.domain.exports.service import AnalysisExportService
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.mattermost import MattermostClient, MattermostPost
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)

logger = get_logger(__name__)


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


@dataclass(frozen=True)
class MattermostConversationContext:
    markdown: str | None


class MattermostResponsePolicy:
    """Decides when a Mattermost post should enter the analytics chat runtime."""

    _DIRECT_CHANNEL_TYPES = frozenset({"D"})

    def should_respond(
        self,
        *,
        event: MattermostPostEvent,
        bot_user_id: str,
        bot_username: str,
        reply_to_bot_post: bool = False,
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
        mentioned_by_id = bot_user_id in (event.mentions or [])
        mentioned_by_name = bool(mention_pattern.search(message))
        if not mentioned_by_id and not mentioned_by_name and not reply_to_bot_post:
            return None

        cleaned = mention_pattern.sub(" ", message).strip()
        return _MattermostQuestion(
            text=cleaned if mentioned_by_name else message,
            thread_key=event.thread_root_id,
            root_id=event.thread_root_id,
        )


class MattermostResponseRenderer:
    """Formats Rhetorica chat responses for Mattermost posts."""

    def render(self, response: ChatAskResponse) -> str:
        answer, sql_from_answer = self._clean_answer(response.answer)
        generated_sql = response.generated_sql or sql_from_answer
        parts = [answer.strip() or "Ответ пустой. Попробуй уточнить вопрос."]
        if generated_sql:
            parts.append(f"```sql\n{generated_sql.strip()}\n```")
        return "\n\n".join(parts)

    @staticmethod
    def _clean_answer(answer: str) -> tuple[str, str | None]:
        normalized = answer.strip()
        if not normalized.startswith("{"):
            return normalized, None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            return normalized, None
        if not isinstance(payload, dict):
            return normalized, None
        cleaned = payload.get("answer_markdown") or payload.get("answer")
        sql = payload.get("sql") or payload.get("generated_sql")
        return (
            cleaned.strip() if isinstance(cleaned, str) and cleaned.strip() else normalized,
            sql.strip() if isinstance(sql, str) and sql.strip() else None,
        )


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
    ) -> MattermostConversationContext:
        if event.root_id or event.parent_id:
            posts = self._thread_posts(client=client, root_id=event.thread_root_id)
            title = "Mattermost thread context:"
        else:
            posts = self._channel_nearby_posts(client=client, event=event)
            title = "Mattermost nearby channel context:"
        markdown = self._format_posts(
            posts=posts,
            title=title,
            bot_user_id=bot_user_id,
        )
        return MattermostConversationContext(markdown=markdown)

    def _thread_posts(
        self,
        *,
        client: MattermostClient,
        root_id: str,
    ) -> list[MattermostPost]:
        try:
            posts = client.get_post_thread(root_id)
        except Exception:
            logger.debug("mattermost.context.thread_fetch_failed", root_id=root_id)
            return []
        return self._sort_posts(posts)[: self._MAX_THREAD_POSTS]

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
        return [*self._sort_posts(previous)[-self._MAX_CHANNEL_CONTEXT_POSTS :], current]

    def _format_posts(
        self,
        *,
        posts: list[MattermostPost],
        title: str,
        bot_user_id: str,
    ) -> str | None:
        lines = [title]
        for post in posts:
            message = self._clean_post_message(post.message)
            if not post.id or not message:
                continue
            author = "bot" if post.user_id == bot_user_id else f"user:{post.user_id}"
            lines.append(f"- {author}: {message}")
        if len(lines) == 1:
            return None
        markdown = "\n".join(lines)
        if len(markdown) <= self._MAX_CONTEXT_CHARS:
            return markdown
        return markdown[: self._MAX_CONTEXT_CHARS].rsplit("\n", 1)[0]

    @staticmethod
    def _sort_posts(posts: list[MattermostPost]) -> list[MattermostPost]:
        return sorted(posts, key=lambda post: (post.create_at, post.id))

    @staticmethod
    def _clean_post_message(message: str) -> str:
        normalized = message.strip()
        if not normalized:
            return ""
        if normalized.startswith("{"):
            try:
                payload = json.loads(normalized)
            except json.JSONDecodeError:
                return normalized
            if isinstance(payload, dict):
                answer = payload.get("answer_markdown") or payload.get("answer")
                if isinstance(answer, str) and answer.strip():
                    return answer.strip()
        return normalized


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
        context_builder: MattermostContextBuilder | None = None,
    ) -> None:
        self._chat_repository = chat_repository
        self._mattermost_repository = mattermost_repository
        self._ask_chat = ask_chat
        self._mattermost_client_factory = mattermost_client_factory
        self._export_service = export_service or AnalysisExportService()
        self._response_policy = response_policy or MattermostResponsePolicy()
        self._response_renderer = response_renderer or MattermostResponseRenderer()
        self._context_builder = context_builder or MattermostContextBuilder()

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

        logger.info(
            "mattermost.post.accepted",
            connector_id=connector.id,
            workspace_id=connector.workspace_id,
            channel_id=event.channel_id,
            post_id=event.post_id,
        )
        conversation_id = self._mattermost_repository.get_or_create_binding(
            workspace_id=connector.workspace_id,
            connector_id=connector.id,
            channel_id=event.channel_id,
            thread_key=question.thread_key,
            conversation_id_factory=lambda: self._create_conversation(connector, event),
            conversation_exists=self._conversation_exists,
        )
        ambient_context = self._context_builder.build(
            client=client,
            event=event,
            bot_user_id=bot_user.id,
        )
        response = self._ask_chat(
            ChatAskRequest(
                workspace_id=connector.workspace_id,
                conversation_id=conversation_id,
                question=question.text,
                run_sql=True,
                ambient_context_markdown=ambient_context.markdown,
            )
        )
        file_ids = self._render_response_files(
            client=client,
            channel_id=event.channel_id,
            question=question.text,
            response=response,
            workspace_id=connector.workspace_id,
        )
        client.create_post(
            channel_id=event.channel_id,
            message=self._response_renderer.render(response),
            root_id=question.root_id or event.root_id,
            file_ids=file_ids or None,
        )
        logger.info(
            "mattermost.post.responded",
            connector_id=connector.id,
            workspace_id=connector.workspace_id,
            channel_id=event.channel_id,
            post_id=event.post_id,
            conversation_id=response.conversation_id or conversation_id,
            file_count=len(file_ids),
        )
        return MattermostHandleResult(
            responded=True,
            conversation_id=response.conversation_id or conversation_id,
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
        reference_id = event.parent_id or event.root_id
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
        )
        return conversation.id

    def _conversation_exists(self, conversation_id: str) -> bool:
        return self._chat_repository.get_conversation(conversation_id) is not None

    def _render_response_files(
        self,
        *,
        client: MattermostClient,
        channel_id: str,
        question: str,
        response: ChatAskResponse,
        workspace_id: str,
    ) -> list[str]:
        chart_png = self._export_service.render_chat_response_chart_png(
            response=response,
            workspace_id=workspace_id,
            question=question,
        )
        if chart_png is None:
            return []
        file_id = client.upload_bytes(
            channel_id=channel_id,
            content=chart_png,
            filename="rhetorica-chart.png",
            content_type="image/png",
        )
        return [file_id]
