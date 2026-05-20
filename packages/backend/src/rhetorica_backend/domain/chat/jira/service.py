"""Jira Server/Data Center HTTP client helpers."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, ClassVar

import httpx
from rhetorica_backend.contracts.admin_integrations import JiraConnectionConfig

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
DEFAULT_USER_AGENT = "Mozilla/5.0"


class JiraService:
    """Thin Jira Server/DC REST wrapper for orchestration tools."""

    DEFAULT_FIELDS: ClassVar[list[str]] = [
        "summary",
        "description",
        "status",
        "assignee",
        "reporter",
        "issuetype",
        "project",
        "created",
        "updated",
    ]

    def _headers(self, connection: JiraConnectionConfig) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if connection.api_token:
            if connection.auth_type == "basic" and connection.username:
                raw = f"{connection.username}:{connection.api_token}"
                encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                headers["Authorization"] = f"Bearer {connection.api_token}"
        return headers

    def _request(
        self,
        connection: JiraConnectionConfig,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{connection.base_url.rstrip('/')}{path}"
        with httpx.Client(timeout=20.0, verify=connection.verify_tls) as client:
            response = client.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=self._headers(connection),
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()

    def search_issues(
        self,
        connection: JiraConnectionConfig,
        *,
        jql: str,
        limit: int,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            connection,
            "POST",
            "/rest/api/2/search",
            json_body={
                "jql": jql,
                "maxResults": limit,
                "startAt": 0,
                "validateQuery": False,
                "fields": fields or list(self.DEFAULT_FIELDS),
            },
        )
        issues = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues, list):
            return []
        return [self._normalize_issue(issue, base_url=connection.base_url) for issue in issues]

    def get_issue(
        self,
        connection: JiraConnectionConfig,
        *,
        issue_key: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self._request(
            connection,
            "GET",
            f"/rest/api/2/issue/{issue_key}",
            params={"fields": ",".join(fields or self.DEFAULT_FIELDS)},
        )
        return self._normalize_issue(payload, base_url=connection.base_url)

    def search_users(
        self,
        connection: JiraConnectionConfig,
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            connection,
            "GET",
            "/rest/api/2/user/search",
            params={"username": query, "maxResults": limit},
        )
        return self._normalize_users(payload)

    def get_myself(
        self,
        connection: JiraConnectionConfig,
    ) -> dict[str, Any]:
        payload = self._request(
            connection,
            "GET",
            "/rest/api/2/myself",
        )
        return self._normalize_user(payload)

    def search_assignable_users(
        self,
        connection: JiraConnectionConfig,
        *,
        project_key: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            connection,
            "GET",
            "/rest/api/2/user/assignable/search",
            params={"project": project_key, "username": query, "maxResults": limit},
        )
        return self._normalize_users(payload)

    def get_create_meta(
        self,
        connection: JiraConnectionConfig,
        *,
        project_key: str,
    ) -> dict[str, Any]:
        try:
            payload = self._request(
                connection,
                "GET",
                "/rest/api/2/issue/createmeta",
                params={
                    "projectKeys": project_key,
                    "expand": "projects.issuetypes.fields",
                },
            )
            return payload if isinstance(payload, dict) else {}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        return self._get_create_meta_v2(connection, project_key=project_key)

    def create_issue(
        self,
        connection: JiraConnectionConfig,
        *,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._request(
            connection,
            "POST",
            "/rest/api/2/issue",
            json_body={"fields": fields},
        )
        if not isinstance(payload, dict):
            return {}
        issue_key = payload.get("key")
        return {
            "id": payload.get("id"),
            "key": issue_key,
            "url": (
                f"{connection.base_url.rstrip('/')}/browse/{issue_key}"
                if isinstance(issue_key, str) and issue_key.strip()
                else None
            ),
        }

    @staticmethod
    def is_issue_key(value: str) -> bool:
        return bool(ISSUE_KEY_PATTERN.fullmatch(value.strip().upper()))

    def _get_create_meta_v2(
        self,
        connection: JiraConnectionConfig,
        *,
        project_key: str,
    ) -> dict[str, Any]:
        issue_types_payload = self._request(
            connection,
            "GET",
            f"/rest/api/2/issue/createmeta/{project_key}/issuetypes",
            params={"maxResults": 1000},
        )
        issue_types = issue_types_payload.get("values") if isinstance(issue_types_payload, dict) else None
        if not isinstance(issue_types, list):
            return {}

        normalized_issue_types: list[dict[str, Any]] = []
        for issue_type in issue_types:
            if not isinstance(issue_type, dict):
                continue
            issue_type_id = issue_type.get("id")
            if not isinstance(issue_type_id, str) or not issue_type_id.strip():
                continue
            fields_payload = self._request(
                connection,
                "GET",
                f"/rest/api/2/issue/createmeta/{project_key}/issuetypes/{issue_type_id}",
                params={"maxResults": 1000},
            )
            field_values = fields_payload.get("values") if isinstance(fields_payload, dict) else None
            fields: dict[str, Any] = {}
            if isinstance(field_values, list):
                for field_meta in field_values:
                    if not isinstance(field_meta, dict):
                        continue
                    field_id = field_meta.get("fieldId")
                    if isinstance(field_id, str) and field_id.strip():
                        fields[field_id] = field_meta

            normalized_issue_types.append(
                {
                    "id": issue_type_id,
                    "name": self._to_optional_str(issue_type.get("name")),
                    "description": self._to_optional_str(issue_type.get("description")),
                    "subtask": bool(issue_type.get("subtask")) if "subtask" in issue_type else None,
                    "fields": fields,
                }
            )

        return {
            "projects": [
                {
                    "key": project_key,
                    "issuetypes": normalized_issue_types,
                }
            ]
        }

    @classmethod
    def _normalize_issue(cls, payload: Any, *, base_url: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        fields = payload.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        key = payload.get("key")
        project = cls._normalize_project(fields.get("project"))
        issue = {
            "id": payload.get("id"),
            "key": key,
            "summary": cls._to_optional_str(fields.get("summary")),
            "description": cls._normalize_description(fields.get("description")),
            "status": cls._nested_name(fields.get("status")),
            "issue_type": cls._nested_name(fields.get("issuetype")),
            "assignee": cls._normalize_user(fields.get("assignee")),
            "reporter": cls._normalize_user(fields.get("reporter")),
            "project": project,
            "created": cls._to_optional_str(fields.get("created")),
            "updated": cls._to_optional_str(fields.get("updated")),
            "url": (
                f"{base_url.rstrip('/')}/browse/{key}"
                if isinstance(key, str) and key.strip()
                else None
            ),
        }
        return {item: value for item, value in issue.items() if value not in (None, {}, [])}

    @classmethod
    def _normalize_project(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        project = {
            "id": payload.get("id"),
            "key": cls._to_optional_str(payload.get("key")),
            "name": cls._to_optional_str(payload.get("name")),
        }
        return {item: value for item, value in project.items() if value not in (None, {}, [])}

    @classmethod
    def _normalize_users(cls, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        users = [cls._normalize_user(item) for item in payload]
        return [item for item in users if item]

    @classmethod
    def _normalize_user(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        user = {
            "name": cls._to_optional_str(payload.get("name")),
            "key": cls._to_optional_str(payload.get("key")),
            "display_name": cls._to_optional_str(payload.get("displayName")),
            "email": cls._to_optional_str(payload.get("emailAddress")),
            "active": bool(payload.get("active")) if "active" in payload else None,
        }
        return {item: value for item, value in user.items() if value not in (None, {}, [])}

    @classmethod
    def _normalize_description(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            return None
        if isinstance(value, dict | list):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip() or None

    @staticmethod
    def _nested_name(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        for key in ("name", "displayName"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def _to_optional_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
