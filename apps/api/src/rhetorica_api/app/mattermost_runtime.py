from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from types import TracebackType
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import websockets
from rhetorica_backend.contracts.admin_integrations import (
    LlmConnectionConfig,
    MattermostConnectorItem,
)
from rhetorica_backend.contracts.chat import ChatAskRequest, ChatAskResponse
from rhetorica_backend.domain.chat.llm import LlmClient
from rhetorica_backend.domain.chat.mattermost import (
    MattermostAmbientDecisionService,
    MattermostPostEvent,
    MattermostTransportService,
)
from rhetorica_backend.infra.logging import get_logger
from rhetorica_backend.infra.mattermost import MattermostClient
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.chat_repository import ChatRepository
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.mattermost_connector_repository import (
    MattermostConnectorRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from sqlalchemy import text
from sqlalchemy.engine import Connection

from rhetorica_api.app.agent_runtime import resolve_agent_for_chat
from rhetorica_api.app.chat_runtime import get_chat_service_factory
from rhetorica_api.app.settings import settings

logger = get_logger(__name__)
logging.getLogger("websockets").setLevel(logging.INFO)

_MattermostEventHandler = Callable[[MattermostPostEvent], Awaitable[None]]


class MattermostEventProcessor:
    """Runs Mattermost post handling with bounded cross-dialog concurrency."""

    def __init__(
        self,
        *,
        connector_id: str,
        worker_count: int,
        queue_size: int,
        handler: _MattermostEventHandler,
    ) -> None:
        self._connector_id = connector_id
        self._handler = handler
        self._run_slots = asyncio.Semaphore(worker_count)
        self._pending_slots = asyncio.BoundedSemaphore(queue_size)
        self._dialog_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False

    async def __aenter__(self) -> MattermostEventProcessor:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc, traceback
        await self.stop(cancel=exc_type is not None)

    async def submit(self, event: MattermostPostEvent) -> None:
        if self._stopping:
            return
        if self._pending_slots.locked():
            logger.warning(
                "mattermost.listener.backpressure",
                connector_id=self._connector_id,
                post_id=event.post_id,
            )
        await self._pending_slots.acquire()
        task = asyncio.create_task(self._run_event(event))
        self._tasks.add(task)
        task.add_done_callback(self._release_task_slot)

    async def stop(self, *, cancel: bool) -> None:
        self._stopping = True
        tasks = list(self._tasks)
        if cancel:
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_event(self, event: MattermostPostEvent) -> None:
        lock = self._dialog_locks.setdefault(_event_dialog_key(event), asyncio.Lock())
        async with lock, self._run_slots:
            try:
                await self._handler(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "mattermost.post.processing_failed",
                    connector_id=self._connector_id,
                    channel_id=event.channel_id,
                    post_id=event.post_id,
                )

    def _release_task_slot(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._pending_slots.release()


class MattermostConnectorLease:
    """Prevents duplicate connector listeners across API replicas on PostgreSQL."""

    def __init__(self, *, runtime: DatabaseRuntime, connector_id: str) -> None:
        self._runtime = runtime
        self._connector_id = connector_id
        self._connection: Connection | None = None
        self._lock_key = _advisory_lock_key(connector_id)

    def acquire(self) -> bool:
        if self._runtime.engine.dialect.name != "postgresql":
            return True
        connection = self._runtime.engine.connect()
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": self._lock_key},
            ).scalar()
        )
        if not acquired:
            connection.close()
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": self._lock_key},
            )
        finally:
            self._connection.close()
            self._connection = None


class MattermostListenerSupervisor:
    """Keeps one websocket listener per enabled Mattermost connector."""

    _SYNC_INTERVAL_SECONDS = 30
    _RECONNECT_SECONDS = 5

    def __init__(self) -> None:
        self._runtime = DatabaseRuntime(settings.database_url)
        self._runtime.create_schema()
        self._repository = MattermostConnectorRepository(db=self._runtime)
        self._chat_repository = ChatRepository(db=self._runtime)
        self._agent_repository = AgentProfileRepository(db=self._runtime)
        self._llm_repository = LlmConnectorRepository(db=self._runtime)
        self._user_repository = UserAccountRepository(db=self._runtime)
        self._tasks: dict[str, asyncio.Task] = {}
        self._fingerprints: dict[str, tuple[object, ...]] = {}
        self._stopping = asyncio.Event()
        self._sync_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._sync_task is None:
            logger.info("mattermost.listener.supervisor.started")
            self._sync_connectors()
            self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        self._stopping.set()
        tasks = list(self._tasks.values())
        if self._sync_task is not None:
            tasks.append(self._sync_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _sync_loop(self) -> None:
        while not self._stopping.is_set():
            self._sync_connectors()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._SYNC_INTERVAL_SECONDS,
                )
            except TimeoutError:
                continue

    def _sync_connectors(self) -> None:
        enabled_connectors = cast(
            list[MattermostConnectorItem],
            self._repository.list_enabled() or [],
        )
        connectors = {
            connector.id: connector
            for connector in enabled_connectors
            if connector.connection.bot_token
        }
        logger.debug(
            "mattermost.listener.supervisor.synced",
            enabled_connectors=len(connectors),
            active_listeners=len(self._tasks),
        )
        for connector_id in set(self._tasks) - set(connectors):
            self._tasks[connector_id].cancel()
            del self._tasks[connector_id]
            self._fingerprints.pop(connector_id, None)
        for connector_id in connectors:
            fingerprint = _connector_fingerprint(connectors[connector_id])
            task = self._tasks.get(connector_id)
            if (
                task is not None
                and not task.done()
                and self._fingerprints.get(connector_id) != fingerprint
            ):
                task.cancel()
                del self._tasks[connector_id]
                task = None
            if task is None or task.done():
                self._fingerprints[connector_id] = fingerprint
                self._tasks[connector_id] = asyncio.create_task(
                    self._listen_connector(connector_id=connector_id)
                )

    async def _listen_connector(self, *, connector_id: str) -> None:
        while not self._stopping.is_set():
            enabled_connectors = cast(
                list[MattermostConnectorItem],
                self._repository.list_enabled() or [],
            )
            connector = next(
                (
                    item
                    for item in enabled_connectors
                    if item.id == connector_id and item.connection.bot_token
                ),
                None,
            )
            if connector is None:
                return
            lease = MattermostConnectorLease(runtime=self._runtime, connector_id=connector_id)
            if not lease.acquire():
                logger.info(
                    "mattermost.listener.lease_not_acquired",
                    connector_id=connector.id,
                    workspace_id=connector.workspace_id,
                )
                await asyncio.sleep(self._RECONNECT_SECONDS)
                continue
            try:
                await self._run_websocket(connector)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "mattermost.listener.failed",
                    connector_id=connector.id,
                    workspace_id=connector.workspace_id,
                )
                await asyncio.sleep(self._RECONNECT_SECONDS)
            finally:
                lease.release()

    async def _run_websocket(self, connector: MattermostConnectorItem) -> None:
        if not connector.connection.bot_token:
            return
        websocket_url = _websocket_url(connector.connection.base_url)
        async with websockets.connect(
            websocket_url,
            **_websocket_connect_kwargs(connector),
        ) as websocket:
            await websocket.send(
                json.dumps({
                    "seq": 1,
                    "action": "authentication_challenge",
                    "data": {"token": connector.connection.bot_token},
                })
            )
            logger.info(
                "mattermost.listener.connected",
                connector_id=connector.id,
                workspace_id=connector.workspace_id,
            )
            async with MattermostEventProcessor(
                connector_id=connector.id,
                worker_count=settings.mattermost_listener_worker_count,
                queue_size=settings.mattermost_listener_queue_size,
                handler=lambda event: self._handle_event(connector=connector, event=event),
            ) as processor:
                async for raw_message in websocket:
                    event = _parse_websocket_post(raw_message)
                    if event is None:
                        continue
                    await processor.submit(event)

    async def _handle_event(
        self,
        *,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
    ) -> None:
        await asyncio.to_thread(
            self._handle_event_sync,
            connector=connector,
            event=event,
        )

    def _handle_event_sync(
        self,
        *,
        connector: MattermostConnectorItem,
        event: MattermostPostEvent,
    ) -> None:
        service = self._build_transport_service(connector)
        service.handle_post(
            connector=connector,
            event=event,
        )

    def _build_transport_service(
        self,
        connector: MattermostConnectorItem,
    ) -> MattermostTransportService:
        factory = get_chat_service_factory()
        orchestrator = factory.create_orchestrator(current_user_id=None)
        return MattermostTransportService(
            chat_repository=self._chat_repository,
            mattermost_repository=self._repository,
            ask_chat=lambda request: self._ask_chat_with_agent_resolution(
                request=request,
                ask_chat=orchestrator.ask,
            ),
            mattermost_client_factory=lambda item: MattermostClient(
                base_url=item.connection.base_url,
                token=item.connection.bot_token or "",
                verify_tls=item.connection.verify_tls,
            ),
            ambient_decision_service=self._build_ambient_decision_service(connector),
        )

    def _build_ambient_decision_service(
        self,
        connector: MattermostConnectorItem,
    ) -> MattermostAmbientDecisionService | None:
        if not connector.connection.ambient_listen_enabled:
            return None
        connection, _agent_prompt = self._resolve_decision_llm_connection(connector)
        if connection is None:
            return None
        return MattermostAmbientDecisionService(
            llm_connection=connection,
            llm_client_factory=LlmClient,
        )

    def _resolve_decision_llm_connection(
        self,
        connector: MattermostConnectorItem,
    ) -> tuple[LlmConnectionConfig | None, str | None]:
        llm_connector_name = None
        agent_prompt = None
        if connector.agent_id:
            current_user = self._user_repository.ensure_bootstrap_user(connector.workspace_id)
            agent = self._agent_repository.get(
                agent_id=connector.agent_id,
                workspace_id=connector.workspace_id,
                current_user_id=current_user.id,
                active_only=True,
            )
            if agent is not None:
                llm_connector_name = agent.llm_connector_name
                agent_prompt = agent.admin_prompt
        return (
            self._llm_repository.get_connection(
                workspace_id=connector.workspace_id,
                name=llm_connector_name,
            ),
            agent_prompt,
        )

    def _ask_chat_with_agent_resolution(
        self,
        *,
        request: ChatAskRequest,
        ask_chat: Callable[[ChatAskRequest], ChatAskResponse],
    ) -> ChatAskResponse:
        current_user = self._user_repository.ensure_bootstrap_user(request.workspace_id)
        existing_conversation = None
        if request.conversation_id is not None:
            existing_conversation = self._chat_repository.get_conversation(
                request.conversation_id,
            )
        resolved_request, _agent = resolve_agent_for_chat(
            request=request,
            agent_repository=self._agent_repository,
            current_user_id=current_user.id,
            existing_conversation=existing_conversation,
        )
        return ask_chat(resolved_request)


def _websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/api/v4/websocket", "", ""))


def _websocket_connect_kwargs(connector: MattermostConnectorItem) -> dict[str, Any]:
    websocket_url = _websocket_url(connector.connection.base_url)
    if websocket_url.startswith("wss://") and not connector.connection.verify_tls:
        return {"ssl": ssl._create_unverified_context()}  # noqa: S323
    return {}


def _connector_fingerprint(
    connector: MattermostConnectorItem,
) -> tuple[object, ...]:
    return (
        connector.connection.base_url,
        connector.connection.bot_token,
        connector.connection.bot_username,
        connector.connection.verify_tls,
        connector.connection.send_sql_enabled,
        connector.connection.ambient_listen_enabled,
        tuple(connector.connection.ambient_channel_ids),
        connector.connection.ambient_decision_prompt,
        connector.agent_id,
    )


def _event_dialog_key(event: MattermostPostEvent) -> str:
    if event.channel_type == "D":
        return f"direct:{event.channel_id}"
    return f"channel:{event.channel_id}:thread:{event.thread_root_id}"


def _advisory_lock_key(connector_id: str) -> int:
    digest = hashlib.blake2b(
        f"mattermost-listener:{connector_id}".encode(),
        digest_size=8,
    ).digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)
    if value >= 2**63:
        return value - 2**64
    return value


def _parse_websocket_post(raw_message: str | bytes) -> MattermostPostEvent | None:
    try:
        payload = json.loads(raw_message)
    except (TypeError, json.JSONDecodeError):
        logger.debug("mattermost.listener.invalid_payload")
        return None
    if not isinstance(payload, dict) or payload.get("event") != "posted":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw_post = data.get("post")
    if not isinstance(raw_post, str):
        return None
    try:
        post = json.loads(raw_post)
    except json.JSONDecodeError:
        logger.debug("mattermost.listener.invalid_post_payload")
        return None
    if not isinstance(post, dict):
        return None
    mentions = _parse_mentions(data.get("mentions"))
    return MattermostPostEvent(
        post_id=str(post.get("id") or ""),
        channel_id=str(post.get("channel_id") or ""),
        user_id=str(post.get("user_id") or ""),
        message=str(post.get("message") or ""),
        channel_type=str(data.get("channel_type") or ""),
        post_type=str(post.get("type") or ""),
        root_id=str(post.get("root_id") or "") or None,
        parent_id=str(post.get("parent_id") or "") or None,
        sender_name=str(data.get("sender_name") or "") or None,
        mentions=mentions,
    )


def _parse_mentions(raw_mentions: object) -> list[str]:
    if isinstance(raw_mentions, list):
        return [str(item) for item in raw_mentions if item]
    if not isinstance(raw_mentions, str) or not raw_mentions:
        return []
    try:
        parsed = json.loads(raw_mentions)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]
