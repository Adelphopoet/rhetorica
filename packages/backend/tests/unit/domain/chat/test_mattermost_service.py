from __future__ import annotations

from datetime import UTC, datetime

from rhetorica_backend.contracts.admin_integrations import (
    MattermostConnectionConfig,
    MattermostConnectorItem,
)
from rhetorica_backend.contracts.chat import ChatAskResponse
from rhetorica_backend.domain.chat.mattermost import (
    MattermostContextBuilder,
    MattermostPostEvent,
    MattermostResponsePolicy,
    MattermostResponseRenderer,
    MattermostTransportService,
)
from rhetorica_backend.infra.mattermost import MattermostPost, MattermostUser


def _connector() -> MattermostConnectorItem:
    return MattermostConnectorItem(
        id="connector-1",
        name="tag-main",
        workspace_id="default",
        connection=MattermostConnectionConfig(
            provider="mattermost",
            base_url="https://tag.example.test",
            bot_token="token",
            bot_username="rhetorica",
        ),
        has_bot_token=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _ChatRepositoryStub:
    def create_conversation(self, **kwargs):
        _ = kwargs

        class Conversation:
            id = "conversation-1"

        return Conversation()

    def get_conversation(self, conversation_id: str):
        _ = conversation_id

        class Conversation:
            id = "conversation-1"

        return Conversation()


class _MattermostRepositoryStub:
    def get_or_create_binding(self, **kwargs):
        factory = kwargs["conversation_id_factory"]
        return factory()


class _MattermostClientStub:
    def __init__(self) -> None:
        self.created_posts: list[dict[str, object]] = []
        self.uploads: list[dict[str, object]] = []
        self.posts: dict[str, MattermostPost] = {}
        self.thread_posts: list[MattermostPost] = []
        self.channel_posts: list[MattermostPost] = []

    def get_me(self) -> MattermostUser:
        return MattermostUser(id="bot-1", username="rhetorica")

    def get_post(self, post_id: str) -> MattermostPost:
        return self.posts[post_id]

    def get_post_thread(self, post_id: str) -> list[MattermostPost]:
        _ = post_id
        return self.thread_posts

    def get_channel_posts_before(
        self,
        *,
        channel_id: str,
        post_id: str,
        per_page: int,
    ) -> list[MattermostPost]:
        _ = channel_id, post_id, per_page
        return self.channel_posts

    def create_post(self, **kwargs) -> str:
        self.created_posts.append(kwargs)
        return "created-post-1"

    def upload_bytes(self, **kwargs) -> str:
        self.uploads.append(kwargs)
        return "file-1"


class _ExportServiceStub:
    def __init__(self, chart_png: bytes | None = None) -> None:
        self._chart_png = chart_png

    def render_chat_response_chart_png(self, **kwargs) -> bytes | None:
        _ = kwargs
        return self._chart_png


def test_mattermost_policy_responds_to_direct_message_without_mention() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="dm-1",
            user_id="user-1",
            message="покажи продажи за вчера",
            channel_type="D",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert question is not None
    assert question.text == "покажи продажи за вчера"
    assert question.thread_key == "dm-1"


def test_mattermost_policy_requires_mention_in_channels() -> None:
    policy = MattermostResponsePolicy()

    ignored = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="покажи продажи",
            channel_type="O",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )
    accepted = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-2",
            channel_id="channel-1",
            user_id="user-1",
            message="@rhetorica покажи продажи",
            channel_type="O",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert ignored is None
    assert accepted is not None
    assert accepted.text == "покажи продажи"
    assert accepted.thread_key == "post-2"


def test_mattermost_policy_accepts_mattermost_mentions_payload() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="покажи продажи",
            channel_type="P",
            mentions=["bot-1"],
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert question is not None
    assert question.text == "покажи продажи"


def test_mattermost_policy_ignores_system_posts_even_with_mentions() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="analytics_bot добавлен(а) в канал пользователем analyst_user.",
            channel_type="O",
            post_type="system_add_to_channel",
            mentions=["bot-1"],
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert question is None


def test_mattermost_policy_ignores_bot_own_posts() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="dm-1",
            user_id="bot-1",
            message="loop",
            channel_type="D",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert question is None


def test_mattermost_policy_accepts_reply_to_bot_without_mention() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-2",
            channel_id="channel-1",
            user_id="user-1",
            message="а теперь по дням",
            channel_type="O",
            root_id="post-1",
            parent_id="bot-post-1",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
        reply_to_bot_post=True,
    )

    assert question is not None
    assert question.text == "а теперь по дням"
    assert question.thread_key == "post-1"


def test_mattermost_response_renderer_keeps_answer_and_sql_only() -> None:
    renderer = MattermostResponseRenderer()

    message = renderer.render(
        ChatAskResponse(
            answer=(
                '{"schema_version":"v1","answer_markdown":"Готово.",'
                '"sql":"SELECT 1","claims":[{"x":1}],"warnings":["nope"]}'
            ),
            generated_sql=None,
        )
    )

    assert message == "Готово.\n\n```sql\nSELECT 1\n```"
    assert "schema_version" not in message
    assert "claims" not in message
    assert "warnings" not in message


def test_mattermost_context_builder_uses_thread_with_caps() -> None:
    client = _MattermostClientStub()
    client.thread_posts = [
        MattermostPost(
            id=f"post-{index}",
            channel_id="channel-1",
            user_id="bot-1" if index == 1 else f"user-{index}",
            message=(
                '{"answer_markdown":"Ответ бота","sql":null}'
                if index == 1
                else f"сообщение {index}"
            ),
            create_at=index,
        )
        for index in range(35)
    ]

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="post-34",
            channel_id="channel-1",
            user_id="user-34",
            message="что дальше?",
            channel_type="O",
            root_id="post-0",
        ),
        bot_user_id="bot-1",
    )

    assert context.markdown is not None
    assert context.markdown.startswith("Mattermost thread context:")
    assert context.markdown.count("\n- ") == 30
    assert "bot: Ответ бота" in context.markdown
    assert "post-34" not in context.markdown


def test_mattermost_context_builder_uses_nearby_channel_messages() -> None:
    client = _MattermostClientStub()
    client.channel_posts = [
        MattermostPost(
            id=f"prev-{index}",
            channel_id="channel-1",
            user_id=f"user-{index}",
            message=f"рядом {index}",
            create_at=index,
        )
        for index in range(20)
    ]

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="current",
            channel_id="channel-1",
            user_id="user-current",
            message="@rhetorica покажи продажи",
            channel_type="O",
        ),
        bot_user_id="bot-1",
    )

    assert context.markdown is not None
    assert context.markdown.startswith("Mattermost nearby channel context:")
    assert context.markdown.count("\n- ") == 13
    assert "рядом 19" in context.markdown
    assert "@rhetorica покажи продажи" in context.markdown


def test_mattermost_transport_renders_clean_message_sql_chart_and_context() -> None:
    client = _MattermostClientStub()
    client.channel_posts = [
        MattermostPost(
            id="prev-1",
            channel_id="channel-1",
            user_id="user-2",
            message="контекст рядом",
            create_at=1,
        )
    ]
    captured_requests = []

    def ask_chat(request):
        captured_requests.append(request)
        return ChatAskResponse(
            answer='{"schema_version":"v1","answer_markdown":"Показал.","sql":null}',
            generated_sql="SELECT 42",
            conversation_id=request.conversation_id,
        )

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=ask_chat,
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(chart_png=b"png"),
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="@rhetorica покажи продажи",
            channel_type="O",
            mentions=["bot-1"],
        ),
    )

    assert result.responded is True
    assert captured_requests[0].question == "покажи продажи"
    assert captured_requests[0].ambient_context_markdown is not None
    assert "контекст рядом" in captured_requests[0].ambient_context_markdown
    assert client.created_posts == [
        {
            "channel_id": "channel-1",
            "message": "Показал.\n\n```sql\nSELECT 42\n```",
            "root_id": "post-1",
            "file_ids": ["file-1"],
        }
    ]


def test_mattermost_transport_accepts_unmentioned_reply_to_bot_post() -> None:
    client = _MattermostClientStub()
    client.posts["bot-post-1"] = MattermostPost(
        id="bot-post-1",
        channel_id="channel-1",
        user_id="bot-1",
        message="старый ответ",
    )

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=lambda request: ChatAskResponse(
            answer="Ок", conversation_id=request.conversation_id
        ),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-2",
            channel_id="channel-1",
            user_id="user-1",
            message="а теперь по дням",
            channel_type="O",
            root_id="thread-1",
            parent_id="bot-post-1",
        ),
    )

    assert result.responded is True
    assert client.created_posts[0]["root_id"] == "thread-1"


def test_mattermost_transport_ignores_unmentioned_reply_not_to_bot_post() -> None:
    client = _MattermostClientStub()
    client.posts["user-post-1"] = MattermostPost(
        id="user-post-1",
        channel_id="channel-1",
        user_id="user-2",
        message="чужой пост",
    )

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=lambda request: ChatAskResponse(answer="не должен вызваться"),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-2",
            channel_id="channel-1",
            user_id="user-1",
            message="а теперь по дням",
            channel_type="O",
            root_id="thread-1",
            parent_id="user-post-1",
        ),
    )

    assert result.responded is False
    assert client.created_posts == []
