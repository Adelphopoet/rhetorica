from __future__ import annotations

import asyncio
import ssl
from datetime import UTC, datetime

import pytest
from rhetorica_api.app.mattermost_runtime import (
    MattermostEventProcessor,
    _connector_fingerprint,
    _parse_websocket_post,
    _websocket_connect_kwargs,
)
from rhetorica_backend.contracts.admin_integrations import (
    MattermostConnectionConfig,
    MattermostConnectorItem,
)
from rhetorica_backend.domain.chat.mattermost import MattermostPostEvent


def _channel_event(
    post_id: str,
    *,
    root_id: str | None = None,
    parent_id: str | None = None,
) -> MattermostPostEvent:
    return MattermostPostEvent(
        post_id=post_id,
        channel_id="channel-1",
        user_id="user-1",
        message="покажи продажи",
        channel_type="O",
        root_id=root_id,
        parent_id=parent_id,
    )


@pytest.mark.asyncio
async def test_mattermost_processor_parallelizes_dialogs_and_serializes_one_thread() -> None:
    started: list[str] = []
    completed: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    same_thread_started = asyncio.Event()
    other_thread_completed = asyncio.Event()

    async def handle_event(event: MattermostPostEvent) -> None:
        started.append(event.post_id)
        if event.post_id == "thread-a":
            first_started.set()
            await release_first.wait()
        elif event.post_id == "reply-a":
            same_thread_started.set()
        elif event.post_id == "thread-b":
            other_thread_completed.set()
        completed.append(event.post_id)

    async with MattermostEventProcessor(
        connector_id="connector-1",
        worker_count=2,
        queue_size=10,
        handler=handle_event,
    ) as processor:
        await processor.submit(_channel_event("thread-a"))
        await asyncio.wait_for(first_started.wait(), timeout=1)

        await processor.submit(_channel_event("reply-a", root_id="thread-a"))
        await processor.submit(_channel_event("thread-b"))

        await asyncio.wait_for(other_thread_completed.wait(), timeout=1)
        assert same_thread_started.is_set() is False

        release_first.set()

    assert completed.index("thread-a") < completed.index("reply-a")
    assert "thread-b" in completed


def test_mattermost_parser_ignores_invalid_payloads() -> None:
    assert _parse_websocket_post("{bad-json") is None
    assert _parse_websocket_post('{"event":"posted","data":{"post":"{bad-json"}}') is None


def test_mattermost_parser_keeps_post_type() -> None:
    event = _parse_websocket_post(
        '{"event":"posted","data":{"channel_type":"O","post":"'
        '{\\"id\\":\\"post-1\\",\\"channel_id\\":\\"channel-1\\",'
        '\\"user_id\\":\\"user-1\\",\\"message\\":\\"joined\\",'
        '\\"type\\":\\"system_add_to_channel\\"}"}}'
    )

    assert event is not None
    assert event.post_type == "system_add_to_channel"


def test_mattermost_websocket_keeps_default_tls_context_for_verified_wss() -> None:
    connector = _connector(verify_tls=True)

    assert _websocket_connect_kwargs(connector) == {}
    assert "secret-token" not in repr(connector)


def test_mattermost_websocket_uses_unverified_context_when_tls_verify_disabled() -> None:
    connector = _connector(verify_tls=False)

    connect_kwargs = _websocket_connect_kwargs(connector)

    assert isinstance(connect_kwargs["ssl"], ssl.SSLContext)


def test_mattermost_fingerprint_changes_when_sql_delivery_setting_changes() -> None:
    disabled = _connector(verify_tls=True, send_sql_enabled=False)
    enabled = _connector(verify_tls=True, send_sql_enabled=True)

    assert _connector_fingerprint(disabled) != _connector_fingerprint(enabled)


def _connector(
    *,
    verify_tls: bool,
    send_sql_enabled: bool = False,
) -> MattermostConnectorItem:
    now = datetime.now(tz=UTC)
    return MattermostConnectorItem(
        id="connector-1",
        name="tag",
        workspace_id="default",
        agent_id=None,
        connection=MattermostConnectionConfig(
            base_url="https://mattermost.example.com",
            bot_token="secret-token",
            bot_username="rhetorica",
            verify_tls=verify_tls,
            send_sql_enabled=send_sql_enabled,
        ),
        has_bot_token=True,
        created_at=now,
        updated_at=now,
    )
