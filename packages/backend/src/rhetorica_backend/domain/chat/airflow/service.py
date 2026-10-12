"""Read-only Airflow REST API helpers."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from rhetorica_backend.contracts.admin_integrations import AirflowConnectionConfig

DEFAULT_USER_AGENT = "Rhetorica Airflow Connector"
MAX_ERROR_BODY_CHARS = 1200


class AirflowRequestError(RuntimeError):
    """Structured Airflow HTTP failure safe to expose to orchestration."""

    def __init__(
        self,
        *,
        status_code: int,
        request_path: str,
        request_params: dict[str, Any],
        response_body: str,
    ) -> None:
        super().__init__(f"Airflow API request failed with HTTP {status_code} for {request_path}")
        self.status_code = status_code
        self.request_path = request_path
        self.request_params = request_params
        self.response_body = response_body

    @classmethod
    def from_response(
        cls,
        response: httpx.Response,
        *,
        request_path: str,
        request_params: dict[str, Any],
    ) -> AirflowRequestError:
        return cls(
            status_code=response.status_code,
            request_path=request_path,
            request_params=request_params,
            response_body=response.text[:MAX_ERROR_BODY_CHARS],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "request_path": self.request_path,
            "request_params": self.request_params,
            "response_body": self.response_body,
        }


class AirflowService:
    """Thin read-only Airflow REST wrapper for DAG and run inspection."""

    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self._client_factory = client_factory
        self._token_cache: dict[tuple[str, str, str], str] = {}

    def search_dags(
        self,
        connection: AirflowConnectionConfig,
        *,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        only_active: bool = True,
        paused: bool | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        bounded_limit = self._bounded_int(
            limit,
            default=connection.dag_limit,
            minimum=1,
            maximum=100,
        )
        params: dict[str, Any] = {
            "limit": bounded_limit,
            "offset": self._bounded_int(offset, default=0, minimum=0, maximum=100000),
            "only_active": only_active,
        }
        if query and query.strip():
            params["dag_id_pattern"] = query.strip()
        if paused is not None:
            params["paused"] = paused
        normalized_tags = self._string_list(tags)
        if normalized_tags:
            params["tags"] = normalized_tags
        payload = self._request_json(connection, self._api_path(connection, "/dags"), params=params)
        dags = payload.get("dags") if isinstance(payload, dict) else None
        return {
            "dags": dags if isinstance(dags, list) else [],
            "total_entries": payload.get("total_entries") if isinstance(payload, dict) else None,
            "limit": bounded_limit,
            "offset": params["offset"],
        }

    def get_dag(self, connection: AirflowConnectionConfig, *, dag_id: str) -> dict[str, Any]:
        return self._request_json(
            connection, self._api_path(connection, f"/dags/{self._quote(dag_id)}")
        )

    def list_dag_runs(
        self,
        connection: AirflowConnectionConfig,
        *,
        dag_id: str,
        limit: int | None = None,
        offset: int = 0,
        state: str | None = None,
        order_by: str | None = "-start_date",
    ) -> dict[str, Any]:
        bounded_limit = self._bounded_int(
            limit,
            default=connection.dag_run_limit,
            minimum=1,
            maximum=100,
        )
        params: dict[str, Any] = {
            "limit": bounded_limit,
            "offset": self._bounded_int(offset, default=0, minimum=0, maximum=100000),
        }
        if state and state.strip():
            params["state"] = state.strip()
        if order_by and order_by.strip():
            params["order_by"] = order_by.strip()
        payload = self._request_json(
            connection,
            self._api_path(connection, f"/dags/{self._quote(dag_id)}/dagRuns"),
            params=params,
        )
        dag_runs = payload.get("dag_runs") if isinstance(payload, dict) else None
        return {
            "dag_runs": dag_runs if isinstance(dag_runs, list) else [],
            "total_entries": payload.get("total_entries") if isinstance(payload, dict) else None,
            "limit": bounded_limit,
            "offset": params["offset"],
        }

    def get_dag_run(
        self,
        connection: AirflowConnectionConfig,
        *,
        dag_id: str,
        dag_run_id: str,
    ) -> dict[str, Any]:
        return self._request_json(
            connection,
            self._api_path(
                connection,
                f"/dags/{self._quote(dag_id)}/dagRuns/{self._quote(dag_run_id)}",
            ),
        )

    def list_task_instances(
        self,
        connection: AirflowConnectionConfig,
        *,
        dag_id: str,
        dag_run_id: str,
        limit: int | None = None,
        offset: int = 0,
        state: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = self._bounded_int(
            limit,
            default=connection.task_limit,
            minimum=1,
            maximum=200,
        )
        params: dict[str, Any] = {
            "limit": bounded_limit,
            "offset": self._bounded_int(offset, default=0, minimum=0, maximum=100000),
        }
        if state and state.strip():
            params["state"] = state.strip()
        payload = self._request_json(
            connection,
            self._api_path(
                connection,
                f"/dags/{self._quote(dag_id)}/dagRuns/{self._quote(dag_run_id)}/taskInstances",
            ),
            params=params,
        )
        task_instances = payload.get("task_instances") if isinstance(payload, dict) else None
        return {
            "task_instances": task_instances if isinstance(task_instances, list) else [],
            "total_entries": payload.get("total_entries") if isinstance(payload, dict) else None,
            "limit": bounded_limit,
            "offset": params["offset"],
        }

    def get_task_logs(
        self,
        connection: AirflowConnectionConfig,
        *,
        dag_id: str,
        dag_run_id: str,
        task_id: str,
        try_number: int,
        full_content: bool = True,
        max_chars: int | None = None,
        offset: int = 0,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        bounded_chars = self._bounded_int(
            max_chars,
            default=connection.log_max_chars,
            minimum=1000,
            maximum=50000,
        )
        bounded_offset = self._bounded_int(offset, default=0, minimum=0, maximum=5_000_000)
        params: dict[str, Any] = {"full_content": full_content}
        if continuation_token and continuation_token.strip():
            params["continuation_token"] = continuation_token.strip()
        payload = self._request_any(
            connection,
            self._api_path(
                connection,
                (
                    f"/dags/{self._quote(dag_id)}/dagRuns/{self._quote(dag_run_id)}"
                    f"/taskInstances/{self._quote(task_id)}/logs/{try_number}"
                ),
            ),
            params=params,
        )
        if isinstance(payload, dict):
            raw_content = payload.get("content")
            content = (
                raw_content
                if isinstance(raw_content, str)
                else self._stringify_log_content(raw_content)
            )
            result = dict(payload)
        else:
            content = str(payload)
            result = {"content": content}
        content_length = len(content)
        chunk = content[bounded_offset : bounded_offset + bounded_chars]
        next_offset = (
            bounded_offset + bounded_chars
            if bounded_offset + bounded_chars < content_length
            else None
        )
        api_continuation = result.get("continuation_token")
        end_of_log = (
            self._is_end_of_log_token(api_continuation)
            if isinstance(api_continuation, str)
            else False
        )
        next_continuation = (
            api_continuation
            if isinstance(api_continuation, str) and api_continuation.strip() and not end_of_log
            else None
        )
        truncated = next_offset is not None or bool(next_continuation)
        result["content"] = chunk
        result["truncated"] = truncated
        result["max_chars"] = bounded_chars
        result["offset"] = bounded_offset
        result["next_offset"] = next_offset
        result["content_length"] = content_length
        result["next_continuation_token"] = next_continuation
        result["end_of_log"] = end_of_log
        if "No logs found" in content:
            result["log_available"] = False
            result["user_message"] = "Airflow returned no remote log content for this task attempt."
        return result

    def list_import_errors(
        self,
        connection: AirflowConnectionConfig,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        bounded_limit = self._bounded_int(limit, default=20, minimum=1, maximum=100)
        params = {
            "limit": bounded_limit,
            "offset": self._bounded_int(offset, default=0, minimum=0, maximum=100000),
        }
        payload = self._request_json(
            connection,
            self._api_path(connection, "/importErrors"),
            params=params,
        )
        import_errors = payload.get("import_errors") if isinstance(payload, dict) else None
        return {
            "import_errors": import_errors if isinstance(import_errors, list) else [],
            "total_entries": payload.get("total_entries") if isinstance(payload, dict) else None,
            "limit": bounded_limit,
            "offset": params["offset"],
        }

    def get_health(self, connection: AirflowConnectionConfig) -> dict[str, Any]:
        if connection.api_path.rstrip("/") == "/api/v2":
            return self._request_json(
                connection, self._api_path(connection, "/monitor/health"), params={}
            )
        return self._request_json(connection, "/health", params={})

    def _request_json(
        self,
        connection: AirflowConnectionConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._request_any(connection, path, params=params)
        return payload if isinstance(payload, dict) else {"content": payload}

    def _request_any(
        self,
        connection: AirflowConnectionConfig,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        request_params = params or {}
        with self._client_factory(
            timeout=connection.timeout_seconds, verify=connection.verify_tls
        ) as client:
            headers = self._headers(connection)
            auth = self._auth(connection)
            token_cache_key: tuple[str, str, str] | None = None
            if self._uses_airflow3_token_auth(connection):
                token_cache_key = self._token_cache_key(connection)
                headers["Authorization"] = f"Bearer {self._get_airflow3_token(client, connection)}"
                auth = None
            response = client.get(
                self._url(connection, path),
                params=request_params,
                headers=headers,
                auth=auth,
            )
            if (
                response.status_code == 401
                and token_cache_key is not None
                and self._token_cache.pop(token_cache_key, None) is not None
            ):
                headers["Authorization"] = f"Bearer {self._get_airflow3_token(client, connection)}"
                response = client.get(
                    self._url(connection, path),
                    params=request_params,
                    headers=headers,
                    auth=auth,
                )
            if response.status_code >= 400:
                raise AirflowRequestError.from_response(
                    response,
                    request_path=path,
                    request_params=request_params,
                )
            if not response.content:
                return {}
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return response.json()
            try:
                return response.json()
            except ValueError:
                return response.text

    def _get_airflow3_token(
        self,
        client: httpx.Client,
        connection: AirflowConnectionConfig,
    ) -> str:
        cache_key = self._token_cache_key(connection)
        cached = self._token_cache.get(cache_key)
        if cached:
            return cached
        response = client.post(
            self._url(connection, "/auth/token"),
            json={
                "username": connection.username,
                "password": connection.password or "",
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
        )
        if response.status_code not in {200, 201}:
            raise AirflowRequestError.from_response(
                response,
                request_path="/auth/token",
                request_params={},
            )
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise AirflowRequestError(
                status_code=response.status_code,
                request_path="/auth/token",
                request_params={},
                response_body="Airflow token response did not include access_token.",
            )
        self._token_cache[cache_key] = token
        return token

    @staticmethod
    def _token_cache_key(connection: AirflowConnectionConfig) -> tuple[str, str, str]:
        return (connection.base_url, connection.username, connection.password or "")

    @staticmethod
    def _headers(_connection: AirflowConnectionConfig) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }

    @staticmethod
    def _auth(connection: AirflowConnectionConfig) -> httpx.BasicAuth | None:
        if AirflowService._uses_airflow3_token_auth(connection):
            return None
        if connection.auth_type != "basic":
            return None
        if not connection.username:
            return None
        return httpx.BasicAuth(connection.username, connection.password or "")

    @staticmethod
    def _uses_airflow3_token_auth(connection: AirflowConnectionConfig) -> bool:
        return connection.auth_type == "basic" and connection.api_path.rstrip("/") == "/api/v2"

    @staticmethod
    def _url(connection: AirflowConnectionConfig, path: str) -> str:
        return f"{connection.base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _api_path(connection: AirflowConnectionConfig, suffix: str) -> str:
        return f"{connection.api_path.rstrip('/')}/{suffix.lstrip('/')}"

    @staticmethod
    def _quote(value: str) -> str:
        return quote(value.strip(), safe="")

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _string_list(value: list[str] | Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _stringify_log_content(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            lines: list[str] = []
            for item in value:
                if isinstance(item, list | tuple) and len(item) >= 2:
                    lines.append(str(item[1]))
                else:
                    lines.append(str(item))
            return "\n".join(lines)
        return str(value)

    @staticmethod
    def _is_end_of_log_token(token: str) -> bool:
        head = token.split(".", 1)[0]
        if not head:
            return False
        padded = head + ("=" * (-len(head) % 4))
        try:
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("end_of_log") is True
