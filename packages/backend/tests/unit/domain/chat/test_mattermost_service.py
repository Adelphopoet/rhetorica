from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    MattermostConnectionConfig,
    MattermostConnectorItem,
)
from rhetorica_backend.contracts.chat import ChatAskResponse
from rhetorica_backend.domain.chat.llm.llm_client import LlmCompletionResponse
from rhetorica_backend.domain.chat.mattermost import (
    MattermostAmbientDecision,
    MattermostAmbientDecisionRequest,
    MattermostAmbientDecisionService,
    MattermostContextBuilder,
    MattermostMessageSplitter,
    MattermostPostEvent,
    MattermostResponsePolicy,
    MattermostResponseRenderer,
    MattermostTransportService,
)
from rhetorica_backend.infra.mattermost import MattermostPost, MattermostUser


def _connector(
    agent_id: str | None = None,
    *,
    send_sql_enabled: bool = False,
) -> MattermostConnectorItem:
    return MattermostConnectorItem(
        id="connector-1",
        name="tag-main",
        workspace_id="default",
        agent_id=agent_id,
        connection=MattermostConnectionConfig(
            provider="mattermost",
            base_url="https://tag.example.test",
            bot_token="token",
            bot_username="rhetorica",
            send_sql_enabled=send_sql_enabled,
        ),
        has_bot_token=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


class _ChatRepositoryStub:
    def __init__(self) -> None:
        self.created_conversations: list[dict[str, object]] = []
        self.source_message_ids: set[str] = set()

    def create_conversation(self, **kwargs):
        self.created_conversations.append(kwargs)

        class Conversation:
            id = "conversation-1"

        return Conversation()

    def get_conversation(self, conversation_id: str):
        _ = conversation_id

        class Conversation:
            id = "conversation-1"

        return Conversation()

    def list_source_message_ids(self, **kwargs):
        _ = kwargs
        return set(self.source_message_ids)


class _MattermostRepositoryStub:
    def __init__(self) -> None:
        self.claimed_events: list[dict[str, object]] = []
        self.completed_events: list[dict[str, object]] = []
        self.duplicate_post_ids: set[str] = set()
        self.conversation_post_ids: set[str] = set()

    def claim_event(self, **kwargs):
        self.claimed_events.append(kwargs)
        return kwargs["post_id"] not in self.duplicate_post_ids

    def complete_event(self, **kwargs):
        self.completed_events.append(kwargs)

    def get_or_create_binding(self, **kwargs):
        factory = kwargs["conversation_id_factory"]
        return factory()

    def list_conversation_post_ids(self, **kwargs):
        _ = kwargs
        return set(self.conversation_post_ids)


class _ReusableMattermostRepositoryStub(_MattermostRepositoryStub):
    def __init__(self) -> None:
        super().__init__()
        self._conversation_id: str | None = None

    def get_or_create_binding(self, **kwargs):
        if self._conversation_id is None:
            self._conversation_id = kwargs["conversation_id_factory"]()
        return self._conversation_id


class _MattermostClientStub:
    def __init__(self) -> None:
        self.created_posts: list[dict[str, object]] = []
        self.created_reactions: list[dict[str, object]] = []
        self.uploads: list[dict[str, object]] = []
        self.posts: dict[str, MattermostPost] = {}
        self.thread_posts: list[MattermostPost] = []
        self.channel_posts: list[MattermostPost] = []
        self.requested_post_ids: list[str] = []
        self.requested_user_ids: list[str] = []
        self.users: dict[str, MattermostUser] = {}
        self.fail_thread_fetch = False

    def get_me(self) -> MattermostUser:
        return MattermostUser(id="bot-1", username="rhetorica")

    def get_user(self, user_id: str) -> MattermostUser:
        self.requested_user_ids.append(user_id)
        return self.users.get(
            user_id,
            MattermostUser(id=user_id, username="mattermost-user", display_name="Mattermost User"),
        )

    def get_post(self, post_id: str) -> MattermostPost:
        self.requested_post_ids.append(post_id)
        return self.posts[post_id]

    def get_post_thread(self, post_id: str) -> list[MattermostPost]:
        _ = post_id
        if self.fail_thread_fetch:
            raise RuntimeError("thread fetch failed")
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

    def create_reaction(self, **kwargs) -> None:
        self.created_reactions.append(kwargs)

    def upload_bytes(self, **kwargs) -> str:
        self.uploads.append(kwargs)
        return f"file-{len(self.uploads)}"


class _AckSchedulerStub:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.callbacks: list[object] = []
        self.canceled: list[bool] = []

    def __call__(self, delay_seconds: float, callback):
        index = len(self.callbacks)
        self.delays.append(delay_seconds)
        self.callbacks.append(callback)
        self.canceled.append(False)

        def cancel() -> None:
            self.canceled[index] = True

        return cancel

    def fire(self, index: int = 0) -> None:
        if not self.canceled[index]:
            callback = self.callbacks[index]
            callback()


class _ExportServiceStub:
    def __init__(
        self,
        chart_png: bytes | None = None,
        chart_pngs: list[bytes] | None = None,
    ) -> None:
        self._chart_png = chart_png
        self._chart_pngs = chart_pngs

    def render_chat_response_chart_png(self, **kwargs) -> bytes | None:
        _ = kwargs
        return self._chart_png

    def render_chat_response_chart_pngs(self, **kwargs) -> list[bytes]:
        _ = kwargs
        if self._chart_pngs is not None:
            return self._chart_pngs
        return [self._chart_png] if self._chart_png is not None else []


class _AmbientDecisionServiceStub:
    def __init__(self, decisions: list[MattermostAmbientDecision]) -> None:
        self.decisions = decisions
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        if not self.decisions:
            raise AssertionError("No ambient decisions left")
        return self.decisions.pop(0)


class _AmbientDecisionLlmClientStub:
    content = ""
    requests: ClassVar[list[dict[str, object]]] = []

    def __init__(self, connection) -> None:
        self.connection = connection

    def chat_completion_with_fallback(self, **kwargs):
        self.requests.append(kwargs)
        return LlmCompletionResponse(message={"content": self.content}, raw_body={}), False, False


def _ambient_decision_service() -> MattermostAmbientDecisionService:
    _AmbientDecisionLlmClientStub.requests = []
    return MattermostAmbientDecisionService(
        llm_connection=LlmConnectionConfig(
            base_url="https://llm.example.test",
            model="ExampleReasoningModel",
            endpoint_path="/chat/completions",
        ),
        llm_client_factory=_AmbientDecisionLlmClientStub,
    )


def _ambient_decision_request() -> MattermostAmbientDecisionRequest:
    return MattermostAmbientDecisionRequest(
        connector_name="tag-main",
        decision_prompt="Answer only when useful.",
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="эй бот, слышишь?",
            channel_type="O",
        ),
        context_markdown="Mattermost thread context:\n- current: эй бот, слышишь?",
    )


def test_ambient_decision_service_parses_json_after_thinking_block() -> None:
    _AmbientDecisionLlmClientStub.content = (
        "<think>internal reasoning</think>\n"
        '{"should_respond": true, "question": "Слышишь меня?", "reason": "direct check"}'
    )
    decision = _ambient_decision_service().decide(_ambient_decision_request())

    assert decision == MattermostAmbientDecision(
        should_respond=True,
        question="Слышишь меня?",
        reason="direct check",
    )
    request = _AmbientDecisionLlmClientStub.requests[0]
    assert request["tools"] == []
    assert request["response_schema"] is not None
    messages = request["messages"]
    assert messages == [
        {"role": "system", "content": "Answer only when useful."},
        {
            "role": "user",
            "content": messages[1]["content"],
        },
    ]
    assert "classification_task" not in messages[1]["content"]
    assert "post" in messages[1]["content"]
    assert "Existing Rhetorica base prompt" not in messages[0]["content"]
    assert "binary classifier" not in messages[0]["content"]


def test_ambient_decision_service_extracts_first_valid_json_object() -> None:
    _AmbientDecisionLlmClientStub.content = (
        "<think>bad scratch {not json}</think>\n"
        "Decision:\n"
        '{"should_respond": false, "question": "", "reason": "small talk"}\n'
    )
    decision = _ambient_decision_service().decide(_ambient_decision_request())

    assert decision.should_respond is False
    assert decision.reason == "small talk"


def test_ambient_decision_service_ignores_without_json() -> None:
    _AmbientDecisionLlmClientStub.content = "<think>only reasoning</think>\nno decision"

    decision = _ambient_decision_service().decide(_ambient_decision_request())

    assert decision.should_respond is False
    assert decision.question == "эй бот, слышишь?"
    assert decision.reason == "mattermost_ambient_decision_invalid_json"


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
    assert accepted.trigger == "explicit"


def test_mattermost_policy_accepts_ambient_allowed_channel_without_mention() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="кто-то обсуждает выручку",
            channel_type="O",
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
        ambient_listen_enabled=True,
        ambient_channel_ids=["channel-1"],
    )

    assert question is not None
    assert question.trigger == "ambient"
    assert question.text == "кто-то обсуждает выручку"
    assert question.thread_key == "post-1"


def test_mattermost_policy_ignores_ambient_when_disabled_or_wrong_channel() -> None:
    policy = MattermostResponsePolicy()
    event = MattermostPostEvent(
        post_id="post-1",
        channel_id="channel-1",
        user_id="user-1",
        message="проверьте метрики",
        channel_type="O",
    )

    assert (
        policy.should_respond(
            event=event,
            bot_user_id="bot-1",
            bot_username="rhetorica",
            ambient_listen_enabled=False,
            ambient_channel_ids=["channel-1"],
        )
        is None
    )
    assert (
        policy.should_respond(
            event=event,
            bot_user_id="bot-1",
            bot_username="rhetorica",
            ambient_listen_enabled=True,
            ambient_channel_ids=["channel-2"],
        )
        is None
    )


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


def test_mattermost_policy_ignores_channel_broadcast_mentions_payload() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message=(
                "@channel\n"
                "[DWH] Обновление компонента Yezzey прод кластера Greenplum"
            ),
            channel_type="O",
            mentions=["bot-1"],
        ),
        bot_user_id="bot-1",
        bot_username="rhetorica",
    )

    assert question is None


def test_mattermost_policy_ignores_system_posts_even_with_mentions() -> None:
    policy = MattermostResponsePolicy()

    question = policy.should_respond(
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="dwh_foodtech_bot добавлен(а) в канал пользователем balov_aa.",
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


def test_mattermost_response_renderer_hides_sql_by_default() -> None:
    renderer = MattermostResponseRenderer()

    message = renderer.render(
        ChatAskResponse(
            answer="Готово.",
            generated_sql="SELECT 1",
        )
    )

    assert message == "Готово."


def test_mattermost_response_renderer_includes_sql_when_enabled() -> None:
    renderer = MattermostResponseRenderer()

    message = renderer.render(
        ChatAskResponse(
            answer="Готово.",
            generated_sql="SELECT 1",
        ),
        send_sql=True,
    )

    assert message == "Готово.\n\n```sql\nSELECT 1\n```"


def test_mattermost_response_renderer_escapes_channel_link_tildes() -> None:
    renderer = MattermostResponseRenderer()

    message = renderer.render(
        ChatAskResponse(
            answer="Структура не меняется: ~64% новичков и ~26% остальных. ~~Стабильно~~.",
        )
    )

    assert message == (
        "Структура не меняется: \\~64% новичков и \\~26% остальных. ~~Стабильно~~."
    )


def test_mattermost_message_splitter_respects_post_limit_and_boundaries() -> None:
    splitter = MattermostMessageSplitter()
    message = f"{'a' * 9_000}\n\n{'b' * 9_000}"

    chunks = splitter.split(message)

    assert chunks == ["a" * 9_000, "b" * 9_000]
    assert all(len(chunk) <= splitter.MAX_POST_CHARACTERS for chunk in chunks)


def test_mattermost_message_splitter_hard_splits_long_unbroken_text() -> None:
    splitter = MattermostMessageSplitter()
    message = "x" * (splitter.MAX_POST_CHARACTERS + 10)

    chunks = splitter.split(message)

    assert [len(chunk) for chunk in chunks] == [
        splitter.MAX_POST_CHARACTERS,
        10,
    ]


def test_mattermost_context_builder_uses_thread_with_caps() -> None:
    client = _MattermostClientStub()
    client.thread_posts = [
        MattermostPost(
            id=f"post-{index}",
            channel_id="channel-1",
            user_id="bot-1" if index == 1 else f"user-{index}",
            message="Ответ бота" if index == 1 else f"сообщение {index}",
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
    assert context.markdown.count("\n- ") == 28
    assert context.markdown.startswith("- Mattermost User:")
    assert "- Бот:" not in context.markdown
    assert "что дальше?" not in context.markdown
    assert context.diagnostics.excluded_current == 1


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
    assert context.markdown.count("\n- ") == 11
    assert context.markdown.startswith("- Mattermost User:")
    assert "рядом 19" in context.markdown
    assert "@rhetorica покажи продажи" not in context.markdown
    assert context.diagnostics.excluded_current == 1


def test_mattermost_context_builder_can_force_thread_context_for_root_post() -> None:
    client = _MattermostClientStub()
    client.users["user-1"] = MattermostUser(
        id="user-1",
        username="mihalko_aa",
        display_name="Анастасия Михалко",
    )
    client.users["user-2"] = MattermostUser(
        id="user-2",
        username="raeva_a_a",
        display_name="Анастасия Раева",
    )
    client.thread_posts = [
        MattermostPost(
            id="root-1",
            channel_id="channel-1",
            user_id="user-1",
            message="обсуждаем новую фичу",
            create_at=1,
        ),
        MattermostPost(
            id="reply-1",
            channel_id="channel-1",
            user_id="user-2",
            message="выглядит нормально",
            root_id="root-1",
            create_at=2,
        ),
    ]
    client.channel_posts = [
        MattermostPost(
            id="nearby-1",
            channel_id="channel-1",
            user_id="user-3",
            message="чужой соседний контекст",
            create_at=0,
        )
    ]

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="root-1",
            channel_id="channel-1",
            user_id="user-1",
            message="обсуждаем новую фичу",
            channel_type="O",
        ),
        bot_user_id="bot-1",
        force_thread_context=True,
    )

    assert context.markdown is not None
    assert "Анастасия Раева" in context.markdown
    assert "user:user-1" not in context.markdown
    assert "user:user-2" not in context.markdown
    assert "обсуждаем новую фичу" not in context.markdown
    assert "выглядит нормально" in context.markdown
    assert "чужой соседний контекст" not in context.markdown


def test_mattermost_context_builder_keeps_current_message_out_of_nearby_context_when_fetch_fails() -> None:
    client = _MattermostClientStub()
    client.fail_thread_fetch = True

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="reply-1",
            channel_id="channel-1",
            user_id="user-1",
            message="а продажи точно просели?",
            channel_type="O",
            root_id="root-1",
        ),
        bot_user_id="bot-1",
    )

    assert context.markdown is None
    assert context.diagnostics.excluded_current == 1


def test_mattermost_context_builder_excludes_bot_current_and_persisted_posts() -> None:
    client = _MattermostClientStub()
    client.channel_posts = [
        MattermostPost(
            id="history-1",
            channel_id="channel-1",
            user_id="user-1",
            message="уже сохранённый вопрос",
            create_at=1,
        ),
        MattermostPost(
            id="bot-1-post",
            channel_id="channel-1",
            user_id="bot-1",
            message="старый ответ бота",
            create_at=2,
        ),
        MattermostPost(
            id="nearby-1",
            channel_id="channel-1",
            user_id="user-2",
            message="новая реплика коллеги",
            create_at=3,
        ),
    ]

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="current",
            channel_id="channel-1",
            user_id="user-3",
            message="текущий вопрос",
            channel_type="O",
        ),
        bot_user_id="bot-1",
        excluded_post_ids={"history-1"},
    )

    assert context.markdown is not None
    assert "новая реплика коллеги" in context.markdown
    assert "уже сохранённый вопрос" not in context.markdown
    assert "старый ответ бота" not in context.markdown
    assert "текущий вопрос" not in context.markdown
    assert context.diagnostics.excluded_history == 1
    assert context.diagnostics.excluded_bot == 1
    assert context.diagnostics.excluded_current == 1
    assert context.diagnostics.retained == 1


def test_mattermost_context_builder_truncates_from_oldest_side() -> None:
    client = _MattermostClientStub()
    client.channel_posts = [
        MattermostPost(
            id="oldest",
            channel_id="channel-1",
            user_id="user-1",
            message=f"OLDEST {'a' * 4_500}",
            create_at=1,
        ),
        MattermostPost(
            id="middle",
            channel_id="channel-1",
            user_id="user-2",
            message=f"MIDDLE {'b' * 4_500}",
            create_at=2,
        ),
        MattermostPost(
            id="newest",
            channel_id="channel-1",
            user_id="user-3",
            message=f"NEWEST {'c' * 4_500}",
            create_at=3,
        ),
    ]

    context = MattermostContextBuilder().build(
        client=client,
        event=MattermostPostEvent(
            post_id="current",
            channel_id="channel-1",
            user_id="user-4",
            message="CURRENT",
            channel_type="O",
        ),
        bot_user_id="bot-1",
    )

    assert context.markdown is not None
    assert len(context.markdown) <= MattermostContextBuilder._MAX_CONTEXT_CHARS
    assert "NEWEST" in context.markdown
    assert "MIDDLE" in context.markdown
    assert "OLDEST" not in context.markdown
    assert "CURRENT" not in context.markdown
    assert context.diagnostics.truncated is True


def test_mattermost_transport_renders_clean_message_sql_chart_and_context() -> None:
    client = _MattermostClientStub()
    ack_scheduler = _AckSchedulerStub()
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
            answer="Показал.",
            generated_sql="SELECT 42",
            conversation_id=request.conversation_id,
        )

    chat_repository = _ChatRepositoryStub()
    mattermost_repository = _MattermostRepositoryStub()
    service = MattermostTransportService(
        chat_repository=chat_repository,
        mattermost_repository=mattermost_repository,
        ask_chat=ask_chat,
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(chart_png=b"png"),
        ack_scheduler=ack_scheduler,
    )

    result = service.handle_post(
        connector=_connector(agent_id="agent-1", send_sql_enabled=True),
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
    assert captured_requests[0].agent_id == "agent-1"
    assert captured_requests[0].actor_context.source == "mattermost"
    assert captured_requests[0].actor_context.login == "mattermost-user"
    assert captured_requests[0].actor_context.display_name == "Mattermost User"
    assert captured_requests[0].actor_context.auth_provider == "mattermost"
    assert captured_requests[0].actor_context.external_user_id == "user-1"
    assert client.requested_user_ids == ["user-2", "user-1"]
    assert captured_requests[0].ambient_context_markdown is not None
    assert "контекст рядом" in captured_requests[0].ambient_context_markdown
    assert captured_requests[0].source_provider == "mattermost"
    assert captured_requests[0].source_message_id == "post-1"
    assert chat_repository.created_conversations[0]["agent_id"] == "agent-1"
    assert mattermost_repository.claimed_events[0]["trigger"] == "explicit"
    assert mattermost_repository.completed_events[0]["status"] == "responded"
    assert ack_scheduler.delays == [5.0]
    assert client.created_reactions == []
    assert ack_scheduler.canceled == [True]
    assert client.created_posts == [
        {
            "channel_id": "channel-1",
            "message": "Показал.\n\n```sql\nSELECT 42\n```",
            "root_id": "post-1",
            "file_ids": ["file-1"],
        }
    ]


def test_mattermost_transport_posts_normal_response_after_recoverable_tool_timeout() -> None:
    client = _MattermostClientStub()
    ack_scheduler = _AckSchedulerStub()

    def ask_chat(request):
        ack_scheduler.fire()
        return ChatAskResponse(
            answer="Продолжил без RAG-инсайтов: использую найденные метаданные.",
            conversation_id=request.conversation_id,
            warnings=["tool_execution_timeout"],
        )

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=ask_chat,
        mattermost_client_factory=lambda connector: client,
        ack_scheduler=ack_scheduler,
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-timeout",
            channel_id="channel-1",
            user_id="user-1",
            message="@rhetorica как считать cancel rate?",
            channel_type="O",
            mentions=["bot-1"],
        ),
    )

    assert result.responded is True
    assert ack_scheduler.delays == [5.0]
    assert client.created_reactions == [
        {
            "user_id": "bot-1",
            "post_id": "post-timeout",
            "emoji_name": "eyes",
        }
    ]
    assert client.created_posts == [
        {
            "channel_id": "channel-1",
            "message": "Продолжил без RAG-инсайтов: использую найденные метаданные.",
            "root_id": "post-timeout",
            "file_ids": None,
        }
    ]


def test_mattermost_transport_splits_long_response_into_multiple_posts() -> None:
    client = _MattermostClientStub()
    splitter = MattermostMessageSplitter()
    answer = f"{'a' * 9_000}\n\n{'b' * 9_000}"
    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=lambda request: ChatAskResponse(
            answer=answer,
            conversation_id=request.conversation_id,
        ),
        mattermost_client_factory=lambda connector: client,
        message_splitter=splitter,
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-long",
            channel_id="channel-1",
            user_id="user-1",
            message="@rhetorica дай подробный ответ",
            channel_type="O",
            mentions=["bot-1"],
        ),
    )

    assert result.responded is True
    assert [post["message"] for post in client.created_posts] == [
        "a" * 9_000,
        "b" * 9_000,
    ]
    assert all(
        len(str(post["message"])) <= splitter.MAX_POST_CHARACTERS
        for post in client.created_posts
    )


def test_mattermost_transport_uploads_every_rendered_chart() -> None:
    client = _MattermostClientStub()

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=lambda request: ChatAskResponse(
            answer="Показал два графика.",
            conversation_id=request.conversation_id,
        ),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(chart_pngs=[b"first", b"second"]),
    )

    result = service.handle_post(
        connector=_connector(),
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="@rhetorica покажи два графика",
            channel_type="O",
            mentions=["bot-1"],
        ),
    )

    assert result.responded is True
    assert [upload["filename"] for upload in client.uploads] == [
        "rhetorica-chart-1.png",
        "rhetorica-chart-2.png",
    ]
    assert client.created_posts[0]["file_ids"] == ["file-1", "file-2"]


def test_mattermost_transport_uses_sender_name_without_user_lookup() -> None:
    client = _MattermostClientStub()
    captured_requests = []

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=_MattermostRepositoryStub(),
        ask_chat=lambda request: captured_requests.append(request)
        or ChatAskResponse(answer="Ок", conversation_id=request.conversation_id),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
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
            sender_name="balov_aa",
        ),
    )

    assert result.responded is True
    assert client.requested_user_ids == []
    assert captured_requests[0].ambient_context_markdown is None
    actor_context = captured_requests[0].actor_context
    assert actor_context.source == "mattermost"
    assert actor_context.login == "balov_aa"
    assert actor_context.display_name == "balov_aa"
    assert actor_context.external_user_id == "user-1"


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


def test_mattermost_transport_ignores_unmentioned_post_in_bot_rooted_thread() -> None:
    client = _MattermostClientStub()
    client.posts["bot-root-1"] = MattermostPost(
        id="bot-root-1",
        channel_id="channel-1",
        user_id="bot-1",
        message="отчет другого скрипта",
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
            message="@another_user тенденция позитивная",
            channel_type="O",
            root_id="bot-root-1",
        ),
    )

    assert result.responded is False
    assert client.created_posts == []
    assert client.requested_post_ids == []


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


def test_mattermost_transport_ambient_false_does_not_create_conversation_or_post() -> None:
    client = _MattermostClientStub()
    repository = _MattermostRepositoryStub()
    chat_repository = _ChatRepositoryStub()
    decider = _AmbientDecisionServiceStub([
        MattermostAmbientDecision(
            should_respond=False,
            question="",
            reason="ordinary chatter",
        )
    ])

    service = MattermostTransportService(
        chat_repository=chat_repository,
        mattermost_repository=repository,
        ask_chat=lambda request: ChatAskResponse(answer="не должен вызваться"),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
        ambient_decision_service=decider,
    )

    connector = _connector().model_copy(
        update={
            "connection": _connector().connection.model_copy(
                update={
                    "ambient_listen_enabled": True,
                    "ambient_channel_ids": ["channel-1"],
                    "ambient_decision_prompt": "Join only for analytics questions.",
                }
            )
        }
    )
    result = service.handle_post(
        connector=connector,
        event=MattermostPostEvent(
            post_id="ambient-1",
            channel_id="channel-1",
            user_id="user-1",
            message="обычный разговор",
            channel_type="O",
        ),
    )

    assert result.responded is False
    assert chat_repository.created_conversations == []
    assert client.created_posts == []
    assert client.created_reactions == []
    assert repository.completed_events[-1]["status"] == "ignored"
    assert decider.requests[0].context_markdown is None
    assert decider.requests[0].event.message == "обычный разговор"


def test_mattermost_transport_ambient_true_responds_with_discussion_context() -> None:
    client = _MattermostClientStub()
    ack_scheduler = _AckSchedulerStub()
    client.thread_posts = [
        MattermostPost(
            id="root-1",
            channel_id="channel-1",
            user_id="user-2",
            message="почему просели продажи?",
            create_at=1,
        )
    ]
    repository = _MattermostRepositoryStub()
    captured_requests = []
    decider = _AmbientDecisionServiceStub([
        MattermostAmbientDecision(
            should_respond=True,
            question="проверь просадку продаж",
            reason="analytics discussion",
        )
    ])

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=repository,
        ask_chat=lambda request: captured_requests.append(request)
        or ChatAskResponse(answer="Проверил.", conversation_id=request.conversation_id),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
        ambient_decision_service=decider,
        ack_scheduler=ack_scheduler,
    )
    connector = _connector().model_copy(
        update={
            "connection": _connector().connection.model_copy(
                update={
                    "ambient_listen_enabled": True,
                    "ambient_channel_ids": ["channel-1"],
                    "ambient_decision_prompt": "Join only for analytics questions.",
                }
            )
        }
    )

    result = service.handle_post(
        connector=connector,
        event=MattermostPostEvent(
            post_id="reply-1",
            channel_id="channel-1",
            user_id="user-1",
            message="кажется это проблема в витрине",
            channel_type="O",
            root_id="root-1",
        ),
    )

    assert result.responded is True
    assert captured_requests[0].question == "кажется это проблема в витрине"
    assert "почему просели продажи?" in captured_requests[0].ambient_context_markdown
    assert "кажется это проблема в витрине" not in captured_requests[0].ambient_context_markdown
    assert client.created_posts[0]["root_id"] == "root-1"
    assert client.created_reactions == []
    assert repository.completed_events[-1]["status"] == "responded"
    assert ack_scheduler.delays == []


def test_mattermost_transport_duplicate_post_does_not_decide_or_chat() -> None:
    client = _MattermostClientStub()
    repository = _MattermostRepositoryStub()
    repository.duplicate_post_ids.add("post-1")
    decider = _AmbientDecisionServiceStub([
        MattermostAmbientDecision(
            should_respond=True,
            question="should not run",
            reason="duplicate",
        )
    ])

    service = MattermostTransportService(
        chat_repository=_ChatRepositoryStub(),
        mattermost_repository=repository,
        ask_chat=lambda request: ChatAskResponse(answer="не должен вызваться"),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
        ambient_decision_service=decider,
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

    assert result.responded is False
    assert decider.requests == []
    assert client.created_posts == []


def test_mattermost_transport_new_thread_message_redecides_after_ambient_ignore() -> None:
    client = _MattermostClientStub()
    repository = _ReusableMattermostRepositoryStub()
    chat_repository = _ChatRepositoryStub()
    captured_requests = []
    decider = _AmbientDecisionServiceStub([
        MattermostAmbientDecision(
            should_respond=False,
            question="",
            reason="not enough",
        ),
        MattermostAmbientDecision(
            should_respond=True,
            question="разберись с продажами",
            reason="new analytics question",
        ),
    ])
    connector = _connector().model_copy(
        update={
            "connection": _connector().connection.model_copy(
                update={
                    "ambient_listen_enabled": True,
                    "ambient_channel_ids": ["channel-1"],
                    "ambient_decision_prompt": "Join only for analytics questions.",
                }
            )
        }
    )
    service = MattermostTransportService(
        chat_repository=chat_repository,
        mattermost_repository=repository,
        ask_chat=lambda request: captured_requests.append(request)
        or ChatAskResponse(answer="Ок", conversation_id=request.conversation_id),
        mattermost_client_factory=lambda connector: client,
        export_service=_ExportServiceStub(),
        ambient_decision_service=decider,
    )

    first = service.handle_post(
        connector=connector,
        event=MattermostPostEvent(
            post_id="post-1",
            channel_id="channel-1",
            user_id="user-1",
            message="непонятный тред",
            channel_type="O",
        ),
    )
    second = service.handle_post(
        connector=connector,
        event=MattermostPostEvent(
            post_id="post-2",
            channel_id="channel-1",
            user_id="user-2",
            message="а продажи точно просели?",
            channel_type="O",
            root_id="post-1",
        ),
    )

    assert first.responded is False
    assert second.responded is True
    assert len(decider.requests) == 2
    assert captured_requests[0].question == "а продажи точно просели?"
    assert chat_repository.created_conversations
