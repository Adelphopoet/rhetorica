from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class MattermostUser:
    id: str
    username: str
    display_name: str | None = None


@dataclass(frozen=True)
class MattermostPost:
    id: str
    channel_id: str
    user_id: str
    message: str
    create_at: int = 0
    root_id: str | None = None
    parent_id: str | None = None


class MattermostClient:
    """Small Mattermost v4 API client used by the bot transport."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_tls: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._verify_tls = verify_tls
        self._timeout_seconds = timeout_seconds

    def get_me(self) -> MattermostUser:
        payload = self._request_json("GET", "/api/v4/users/me")
        return self._user_from_payload(payload)

    def get_user(self, user_id: str) -> MattermostUser:
        payload = self._request_json("GET", f"/api/v4/users/{user_id}")
        return self._user_from_payload(payload)

    def get_post(self, post_id: str) -> MattermostPost:
        payload = self._request_json("GET", f"/api/v4/posts/{post_id}")
        return self._post_from_payload(payload)

    def get_post_thread(self, post_id: str) -> list[MattermostPost]:
        payload = self._request_json("GET", f"/api/v4/posts/{post_id}/thread")
        return self._posts_from_collection_payload(payload)

    def get_channel_posts_before(
        self,
        *,
        channel_id: str,
        post_id: str,
        per_page: int,
    ) -> list[MattermostPost]:
        payload = self._request_json(
            "GET",
            f"/api/v4/channels/{channel_id}/posts",
            params={"before": post_id, "per_page": per_page},
        )
        return self._posts_from_collection_payload(payload)

    def create_post(
        self,
        *,
        channel_id: str,
        message: str,
        root_id: str | None = None,
        file_ids: list[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "channel_id": channel_id,
            "message": message,
        }
        if root_id:
            payload["root_id"] = root_id
        if file_ids:
            payload["file_ids"] = file_ids
        response = self._request_json("POST", "/api/v4/posts", json=payload)
        return str(response["id"])

    def create_reaction(
        self,
        *,
        user_id: str,
        post_id: str,
        emoji_name: str,
    ) -> None:
        self._request_json(
            "POST",
            "/api/v4/reactions",
            json={
                "user_id": user_id,
                "post_id": post_id,
                "emoji_name": emoji_name,
            },
        )

    def upload_bytes(
        self,
        *,
        channel_id: str,
        content: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        files = {
            "channel_id": (None, channel_id),
            "files": (filename, content, content_type),
        }
        with httpx.Client(
            verify=self._verify_tls,
            timeout=self._timeout_seconds,
            headers=self._headers,
        ) as client:
            response = client.post(f"{self._base_url}/api/v4/files", files=files)
            response.raise_for_status()
            payload = response.json()
        file_infos = payload.get("file_infos")
        if not isinstance(file_infos, list) or not file_infos:
            raise ValueError("mattermost_upload_missing_file_info")
        file_id = file_infos[0].get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("mattermost_upload_missing_file_id")
        return file_id

    def upload_file(
        self,
        *,
        channel_id: str,
        path: Path,
        filename: str | None = None,
    ) -> str:
        with path.open("rb") as handle:
            files = {
                "channel_id": (None, channel_id),
                "files": (filename or path.name, handle),
            }
            with httpx.Client(
                verify=self._verify_tls,
                timeout=self._timeout_seconds,
                headers=self._headers,
            ) as client:
                response = client.post(f"{self._base_url}/api/v4/files", files=files)
                response.raise_for_status()
                payload = response.json()
        file_infos = payload.get("file_infos")
        if not isinstance(file_infos, list) or not file_infos:
            raise ValueError("mattermost_upload_missing_file_info")
        file_id = file_infos[0].get("id")
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("mattermost_upload_missing_file_id")
        return file_id

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        with httpx.Client(
            verify=self._verify_tls,
            timeout=self._timeout_seconds,
            headers=self._headers,
        ) as client:
            response = client.request(method, f"{self._base_url}{path}", **kwargs)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("mattermost_unexpected_response")
        return payload

    @classmethod
    def _posts_from_collection_payload(cls, payload: dict[str, Any]) -> list[MattermostPost]:
        raw_posts = payload.get("posts")
        if not isinstance(raw_posts, dict):
            return []
        order = payload.get("order")
        if not isinstance(order, list):
            order = payload.get("posts_order")
        if not isinstance(order, list):
            order = sorted(
                raw_posts,
                key=lambda post_id: int(
                    raw_posts[post_id].get("create_at", 0)
                    if isinstance(raw_posts.get(post_id), dict)
                    else 0
                ),
            )
        posts: list[MattermostPost] = []
        for post_id in order:
            raw_post = raw_posts.get(str(post_id))
            if isinstance(raw_post, dict):
                posts.append(cls._post_from_payload(raw_post))
        return posts

    @staticmethod
    def _user_from_payload(payload: dict[str, Any]) -> MattermostUser:
        display_name = str(payload.get("nickname") or "").strip()
        if not display_name:
            full_name = " ".join(
                part
                for part in (
                    str(payload.get("first_name") or "").strip(),
                    str(payload.get("last_name") or "").strip(),
                )
                if part
            )
            display_name = full_name
        return MattermostUser(
            id=str(payload["id"]),
            username=str(payload["username"]),
            display_name=display_name or None,
        )

    @staticmethod
    def _post_from_payload(payload: dict[str, Any]) -> MattermostPost:
        return MattermostPost(
            id=str(payload.get("id") or ""),
            channel_id=str(payload.get("channel_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            message=str(payload.get("message") or ""),
            create_at=int(payload.get("create_at") or 0),
            root_id=str(payload.get("root_id") or "") or None,
            parent_id=str(payload.get("parent_id") or "") or None,
        )
