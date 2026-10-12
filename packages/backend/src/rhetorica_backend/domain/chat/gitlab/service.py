"""GitLab REST helpers: read tools plus a fenced commit/Draft-MR write surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig

DEFAULT_USER_AGENT = "Rhetorica GitLab Connector"
APPROX_CHARS_PER_TOKEN = 4
MAX_ERROR_BODY_CHARS = 1200


class GitLabRequestError(RuntimeError):
    """Structured GitLab HTTP failure safe to expose to orchestration."""

    def __init__(
        self,
        *,
        status_code: int,
        request_path: str,
        request_params: dict[str, Any],
        response_body: str,
    ) -> None:
        super().__init__(f"GitLab API request failed with HTTP {status_code} for {request_path}")
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
    ) -> GitLabRequestError:
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


# Commits and Merge Requests REST endpoints require `api`; `write_repository` is Git-over-HTTP only.
WRITE_TOKEN_SCOPES = frozenset({"api"})
COMMIT_DIFF_PAGE_SIZE = 100
MAX_COMMIT_DIFF_PAGES = 100


class GitLabService:
    """Thin GitLab REST wrapper for bounded search, file reads, and fenced publication."""

    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self._client_factory = client_factory

    def resolve_commit(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        ref: str,
    ) -> str:
        """Resolve an import ref once, so every imported asset has one SHA."""
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._request_json(
                client=client,
                connection=connection,
                path=(
                    f"/api/v4/projects/{self._quote_path(project_path)}/repository/commits/"
                    f"{self._quote_path(ref)}"
                ),
            )
        commit_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(commit_id, str) or not commit_id:
            raise GitLabRequestError(
                status_code=502,
                request_path="repository/commits",
                request_params={"ref": ref},
                response_body="commit SHA missing",
            )
        return commit_id

    def search_content(
        self,
        connection: GitLabConnectionConfig,
        *,
        query: str,
        limit: int,
        project_paths: list[str] | None = None,
        group_paths: list[str] | None = None,
        ref: str | None = None,
        path_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = self._bounded_int(
            limit, default=connection.search_limit, minimum=1, maximum=10
        )
        projects = self._normalized_scope(project_paths) or connection.default_project_paths
        groups = self._normalized_scope(group_paths) or connection.default_group_paths
        if not projects and not groups:
            return []
        search_query = self._search_query(query=query, path_filter=path_filter)

        results: list[dict[str, Any]] = []
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            for project in projects:
                payload = self._request_json(
                    client=client,
                    connection=connection,
                    path=f"/api/v4/projects/{self._quote_path(project)}/search",
                    params={
                        "scope": "blobs",
                        "search": search_query,
                        "per_page": min(bounded_limit, 10),
                        **({"ref": ref} if ref else {}),
                    },
                )
                results.extend(
                    self._normalize_search_items(
                        payload,
                        base_url=connection.base_url,
                        project_hint=project,
                    )
                )
                if len(results) >= bounded_limit:
                    return results[:bounded_limit]

            for group in groups:
                payload = self._request_json(
                    client=client,
                    connection=connection,
                    path=f"/api/v4/groups/{self._quote_path(group)}/search",
                    params={
                        "scope": "blobs",
                        "search": search_query,
                        "per_page": min(bounded_limit, 10),
                        **({"ref": ref} if ref else {}),
                    },
                )
                results.extend(
                    self._normalize_search_items(
                        payload,
                        base_url=connection.base_url,
                        group_hint=group,
                    )
                )
                if len(results) >= bounded_limit:
                    return results[:bounded_limit]
        return results[:bounded_limit]

    def find_paths(
        self,
        connection: GitLabConnectionConfig,
        *,
        query: str,
        limit: int,
        project_paths: list[str] | None = None,
        ref: str | None = None,
        path: str | None = None,
        recursive: bool = True,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        bounded_limit = self._bounded_int(limit, default=20, minimum=1, maximum=50)
        bounded_pages = self._bounded_int(max_pages, default=10, minimum=1, maximum=50)
        projects = self._normalized_scope(project_paths) or connection.default_project_paths
        normalized_query = query.strip().casefold()
        normalized_path = path.strip().strip("/") if isinstance(path, str) else ""
        if not projects or not normalized_query:
            return {
                "query": query,
                "path": normalized_path or None,
                "ref": ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD",
                "recursive": recursive,
                "matches": [],
                "scanned_items": 0,
                "scanned_pages": 0,
                "truncated": False,
                "next_page": None,
            }

        matches: list[dict[str, Any]] = []
        scanned_items = 0
        scanned_pages = 0
        truncated = False
        next_page: str | None = None
        resolved_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            for project in projects:
                page = "1"
                for _ in range(bounded_pages):
                    payload, headers = self._request_json_with_headers(
                        client=client,
                        connection=connection,
                        path=f"/api/v4/projects/{self._quote_path(project)}/repository/tree",
                        params={
                            "per_page": 100,
                            "page": page,
                            "ref": resolved_ref,
                            "recursive": recursive,
                            **({"path": normalized_path} if normalized_path else {}),
                        },
                    )
                    scanned_pages += 1
                    items = self._normalize_tree_items(
                        payload,
                        base_url=connection.base_url,
                        project_path=project,
                        ref=resolved_ref,
                    )
                    scanned_items += len(items)
                    for item in items:
                        item_path = self._optional_str(item.get("path")) or ""
                        item_name = self._optional_str(item.get("name")) or ""
                        path_match = normalized_query in item_path.casefold()
                        name_match = normalized_query in item_name.casefold()
                        if not path_match and not name_match:
                            continue
                        matches.append({
                            **item,
                            "match_on": "path" if path_match else "name",
                        })
                        if len(matches) >= bounded_limit:
                            next_page = self._next_page(headers)
                            truncated = True
                            return {
                                "query": query,
                                "path": normalized_path or None,
                                "ref": resolved_ref,
                                "recursive": recursive,
                                "matches": matches,
                                "scanned_items": scanned_items,
                                "scanned_pages": scanned_pages,
                                "truncated": truncated,
                                "next_page": next_page,
                            }
                    next_page = self._next_page(headers)
                    if not next_page:
                        break
                    page = next_page
                else:
                    truncated = True
                    break

        return {
            "query": query,
            "path": normalized_path or None,
            "ref": resolved_ref,
            "recursive": recursive,
            "matches": matches,
            "scanned_items": scanned_items,
            "scanned_pages": scanned_pages,
            "truncated": truncated,
            "next_page": next_page if truncated else None,
        }

    def read_file(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        file_path: str,
        ref: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        resolved_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
        bounded_tokens = self._bounded_int(
            max_tokens,
            default=connection.read_max_tokens,
            minimum=200,
            maximum=4000,
        )
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            content = self._request_text(
                client=client,
                connection=connection,
                path=(
                    f"/api/v4/projects/{self._quote_path(project_path)}/repository/files/"
                    f"{self._quote_path(file_path)}/raw"
                ),
                params={"ref": resolved_ref},
            )

        selected_content, selected_start, selected_end = self._slice_lines(
            content,
            start_line=start_line,
            end_line=end_line,
        )
        trimmed, truncated = self._trim_to_tokens(selected_content, max_tokens=bounded_tokens)
        return {
            "project_path": project_path.strip().strip("/"),
            "file_path": file_path.strip().strip("/"),
            "ref": resolved_ref,
            "start_line": selected_start,
            "end_line": selected_end,
            "content": trimmed,
            "truncated": truncated,
            "max_tokens": bounded_tokens,
            "url": self._file_url(
                base_url=connection.base_url,
                project_path=project_path,
                ref=resolved_ref,
                file_path=file_path,
            ),
        }

    def list_tree(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        path: str | None = None,
        ref: str | None = None,
        recursive: bool = False,
        limit: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        resolved_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
        bounded_limit = self._bounded_int(
            limit,
            default=50,
            minimum=1,
            maximum=100,
        )
        bounded_page = self._bounded_int(page, default=1, minimum=1, maximum=1000)
        normalized_path = path.strip().strip("/") if isinstance(path, str) else ""
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload, headers = self._request_json_with_headers(
                client=client,
                connection=connection,
                path=f"/api/v4/projects/{self._quote_path(project_path)}/repository/tree",
                params={
                    "per_page": bounded_limit,
                    "page": bounded_page,
                    "ref": resolved_ref,
                    "recursive": recursive,
                    **({"path": normalized_path} if normalized_path else {}),
                },
            )
        next_page = self._next_page(headers)

        return {
            "project_path": project_path.strip().strip("/"),
            "path": normalized_path or None,
            "ref": resolved_ref,
            "recursive": recursive,
            "limit": bounded_limit,
            "page": bounded_page,
            "next_page": next_page,
            "truncated": bool(next_page),
            "items": self._normalize_tree_items(
                payload,
                base_url=connection.base_url,
                project_path=project_path,
                ref=resolved_ref,
            ),
        }

    def get_file_blame(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        file_path: str,
        start_line: int,
        end_line: int,
        ref: str | None = None,
    ) -> dict[str, Any]:
        resolved_ref = ref.strip() if isinstance(ref, str) and ref.strip() else "HEAD"
        start = max(1, start_line)
        end = max(start, min(end_line, start + 99))
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._request_json(
                client=client,
                connection=connection,
                path=(
                    f"/api/v4/projects/{self._quote_path(project_path)}/repository/files/"
                    f"{self._quote_path(file_path)}/blame"
                ),
                params={
                    "ref": resolved_ref,
                    "range[start]": start,
                    "range[end]": end,
                },
            )

        return {
            "project_path": project_path.strip().strip("/"),
            "file_path": file_path.strip().strip("/"),
            "ref": resolved_ref,
            "start_line": start,
            "end_line": end,
            "ranges": self._normalize_blame_ranges(payload, start_line=start),
            "url": self._file_url(
                base_url=connection.base_url,
                project_path=project_path,
                ref=resolved_ref,
                file_path=file_path,
            ),
        }

    def download_repository_archive(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        commit_sha: str,
        max_bytes: int,
    ) -> bytes:
        """Download one commit-pinned archive without exposing connector credentials."""
        self._require_commit_sha(commit_sha)
        path = f"/api/v4/projects/{self._quote_path(project_path)}/repository/archive.tar.gz"
        with (
            self._client_factory(timeout=60.0, verify=connection.verify_tls) as client,
            client.stream(
                "GET",
                f"{connection.base_url.rstrip('/')}{path}",
                params={"sha": commit_sha},
                headers=self._headers(connection),
            ) as response,
        ):
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise GitLabRequestError.from_response(
                    response,
                    request_path=path,
                    request_params={"sha": commit_sha},
                ) from exc
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("gitlab_repository_archive_too_large")
                chunks.append(chunk)
        return b"".join(chunks)

    def get_branch(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        branch: str,
    ) -> dict[str, Any] | None:
        path = (
            f"/api/v4/projects/{self._quote_path(project_path)}"
            f"/repository/branches/{self._quote_path(branch)}"
        )
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            return self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={},
                missing_ok=True,
            )

    def get_commit(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        sha: str,
    ) -> dict[str, Any]:
        self._require_commit_sha(sha)
        path = (
            f"/api/v4/projects/{self._quote_path(project_path)}"
            f"/repository/commits/{self._quote_path(sha)}"
        )
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={},
            )
        if not isinstance(payload, dict):
            raise TypeError("gitlab_commit_payload_invalid")
        return payload

    def get_commit_diff(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        sha: str,
        max_pages: int = MAX_COMMIT_DIFF_PAGES,
    ) -> list[dict[str, Any]]:
        """Return every diff entry of a commit; truncation raises instead of a partial list."""
        self._require_commit_sha(sha)
        path = (
            f"/api/v4/projects/{self._quote_path(project_path)}"
            f"/repository/commits/{self._quote_path(sha)}/diff"
        )
        entries: list[dict[str, Any]] = []
        page = "1"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            for _ in range(max_pages):
                payload, headers = self._send_json_with_headers(
                    client=client,
                    connection=connection,
                    method="GET",
                    path=path,
                    params={"per_page": COMMIT_DIFF_PAGE_SIZE, "page": page},
                )
                if isinstance(payload, list):
                    entries.extend(item for item in payload if isinstance(item, dict))
                next_page = self._next_page(headers)
                if not next_page:
                    return entries
                page = next_page
        raise ValueError("gitlab_commit_diff_too_large")

    def get_project(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
    ) -> dict[str, Any]:
        path = f"/api/v4/projects/{self._quote_path(project_path)}"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={},
            )
        if not isinstance(payload, dict):
            raise TypeError("gitlab_project_payload_invalid")
        return payload

    def list_commits(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        ref: str,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        bounded = self._bounded_int(per_page, default=20, minimum=1, maximum=100)
        path = f"/api/v4/projects/{self._quote_path(project_path)}/repository/commits"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={"ref_name": ref, "per_page": bounded},
            )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def create_commit(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        branch: str,
        start_sha: str,
        commit_message: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._require_commit_sha(start_sha)
        if not branch.strip() or not commit_message.strip() or not actions:
            raise ValueError("gitlab_commit_request_invalid")
        path = f"/api/v4/projects/{self._quote_path(project_path)}/repository/commits"
        with self._client_factory(timeout=60.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="POST",
                path=path,
                params={},
                json_body={
                    "branch": branch.strip(),
                    "start_sha": start_sha,
                    "commit_message": commit_message,
                    "actions": actions,
                },
            )
        if not isinstance(payload, dict):
            raise TypeError("gitlab_commit_payload_invalid")
        return payload

    def list_merge_requests(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        source_branch: str,
        target_branch: str,
    ) -> list[dict[str, Any]]:
        path = f"/api/v4/projects/{self._quote_path(project_path)}/merge_requests"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "state": "all",
                    "per_page": 20,
                },
            )
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def create_draft_merge_request(
        self,
        connection: GitLabConnectionConfig,
        *,
        project_path: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ) -> dict[str, Any]:
        draft_title = title if title.lower().startswith("draft:") else f"Draft: {title}"
        path = f"/api/v4/projects/{self._quote_path(project_path)}/merge_requests"
        with self._client_factory(timeout=30.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="POST",
                path=path,
                params={},
                json_body={
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "title": draft_title,
                    "description": description,
                    "draft": True,
                },
            )
        if not isinstance(payload, dict):
            raise TypeError("gitlab_merge_request_payload_invalid")
        return payload

    def get_access_token_info(
        self,
        connection: GitLabConnectionConfig,
    ) -> dict[str, Any] | None:
        path = "/api/v4/personal_access_tokens/self"
        with self._client_factory(timeout=20.0, verify=connection.verify_tls) as client:
            payload = self._send_json(
                client=client,
                connection=connection,
                method="GET",
                path=path,
                params={},
                missing_ok=True,
            )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise TypeError("gitlab_token_info_payload_invalid")
        return payload

    def _request_json(
        self,
        *,
        client: httpx.Client,
        connection: GitLabConnectionConfig,
        path: str,
        params: dict[str, Any],
    ) -> Any:
        payload, _headers = self._request_json_with_headers(
            client=client,
            connection=connection,
            path=path,
            params=params,
        )
        return payload

    def _request_json_with_headers(
        self,
        *,
        client: httpx.Client,
        connection: GitLabConnectionConfig,
        path: str,
        params: dict[str, Any],
    ) -> tuple[Any, httpx.Headers]:
        payload, headers = self._send_json_with_headers(
            client=client,
            connection=connection,
            method="GET",
            path=path,
            params=params,
        )
        return payload, headers

    def _send_json(
        self,
        *,
        client: httpx.Client,
        connection: GitLabConnectionConfig,
        method: str,
        path: str,
        params: dict[str, Any],
        json_body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        payload, _headers = self._send_json_with_headers(
            client=client,
            connection=connection,
            method=method,
            path=path,
            params=params,
            json_body=json_body,
            missing_ok=missing_ok,
        )
        return payload

    def _send_json_with_headers(
        self,
        *,
        client: httpx.Client,
        connection: GitLabConnectionConfig,
        method: str,
        path: str,
        params: dict[str, Any],
        json_body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> tuple[Any, httpx.Headers]:
        verb = method.upper()
        if verb not in {"GET", "POST"}:
            raise ValueError("gitlab_http_method_not_allowed")
        headers = self._headers(connection)
        if json_body is not None:
            headers = {**headers, "Content-Type": "application/json"}
        response = client.request(
            verb,
            f"{connection.base_url.rstrip('/')}{path}",
            params=params,
            json=json_body,
            headers=headers,
        )
        if missing_ok and response.status_code == 404:
            return None, response.headers
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitLabRequestError.from_response(
                response,
                request_path=path,
                request_params=params if json_body is None else {**params, "json": True},
            ) from exc
        if not response.content:
            return {}, response.headers
        return response.json(), response.headers

    def _request_text(
        self,
        *,
        client: httpx.Client,
        connection: GitLabConnectionConfig,
        path: str,
        params: dict[str, Any],
    ) -> str:
        response = client.get(
            f"{connection.base_url.rstrip('/')}{path}",
            params=params,
            headers=self._headers(connection),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitLabRequestError.from_response(
                response,
                request_path=path,
                request_params=params,
            ) from exc
        return response.text

    @staticmethod
    def _require_commit_sha(commit_sha: str) -> None:
        if not commit_sha or any(
            character not in "0123456789abcdefABCDEF" for character in commit_sha
        ):
            raise ValueError("gitlab_commit_sha_invalid")

    @staticmethod
    def _headers(connection: GitLabConnectionConfig) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        if connection.api_token:
            headers["PRIVATE-TOKEN"] = connection.api_token
        return headers

    @classmethod
    def _normalize_search_items(
        cls,
        payload: Any,
        *,
        base_url: str,
        project_hint: str | None = None,
        group_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        results: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = cls._optional_str(item.get("path") or item.get("filename"))
            if path is None:
                continue
            project_path = cls._optional_str(
                item.get("project_path")
                or item.get("project_full_path")
                or item.get("project_name")
                or project_hint
            )
            ref = cls._optional_str(item.get("ref"))
            startline = item.get("startline")
            data = cls._optional_str(item.get("data"))
            snippet = cls._compact_snippet(data)
            result = {
                "project_path": project_path,
                "project_id": item.get("project_id"),
                "group_path": group_hint,
                "file_path": path,
                "ref": ref,
                "startline": startline if isinstance(startline, int) else None,
                "snippet": snippet,
            }
            if project_path and ref:
                result["url"] = cls._file_url(
                    base_url=base_url,
                    project_path=project_path,
                    ref=ref,
                    file_path=path,
                )
            results.append(result)
        return results

    @classmethod
    def _normalize_tree_items(
        cls,
        payload: Any,
        *,
        base_url: str,
        project_path: str,
        ref: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        items: list[dict[str, Any]] = []
        normalized_project = project_path.strip().strip("/")
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = cls._optional_str(item.get("path"))
            item_type = cls._optional_str(item.get("type"))
            if path is None or item_type not in {"blob", "tree"}:
                continue
            result = {
                "project_path": normalized_project,
                "file_path": path if item_type == "blob" else None,
                "path": path,
                "name": cls._optional_str(item.get("name")),
                "type": item_type,
                "mode": cls._optional_str(item.get("mode")),
                "id": cls._optional_str(item.get("id")),
            }
            if item_type == "blob":
                result["url"] = cls._file_url(
                    base_url=base_url,
                    project_path=normalized_project,
                    ref=ref,
                    file_path=path,
                )
            else:
                result["url"] = (
                    f"{base_url.rstrip('/')}/{normalized_project}/-/tree/"
                    f"{quote(ref, safe='')}/{path.strip().strip('/')}"
                )
            items.append(result)
        return items

    @classmethod
    def _normalize_blame_ranges(
        cls,
        payload: Any,
        *,
        start_line: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        ranges: list[dict[str, Any]] = []
        next_line = start_line
        for item in payload:
            if not isinstance(item, dict):
                continue
            lines = item.get("lines")
            if not isinstance(lines, list):
                continue
            text_lines = [line for line in lines if isinstance(line, str)]
            if not text_lines:
                continue
            range_start = next_line
            range_end = range_start + len(text_lines) - 1
            next_line = range_end + 1
            commit = item.get("commit")
            commit_payload = commit if isinstance(commit, dict) else {}
            ranges.append({
                "start_line": range_start,
                "end_line": range_end,
                "line_count": len(text_lines),
                "snippet": cls._compact_snippet("\n".join(text_lines)),
                "commit_id": cls._optional_str(commit_payload.get("id")),
                "short_id": cls._optional_str(commit_payload.get("short_id")),
                "title": cls._optional_str(commit_payload.get("title")),
                "author_name": cls._optional_str(commit_payload.get("author_name")),
                "author_email": cls._optional_str(commit_payload.get("author_email")),
                "authored_date": cls._optional_str(commit_payload.get("authored_date")),
                "committed_date": cls._optional_str(commit_payload.get("committed_date")),
            })
        return ranges

    @staticmethod
    def _slice_lines(
        content: str,
        *,
        start_line: int | None,
        end_line: int | None,
    ) -> tuple[str, int, int]:
        lines = content.splitlines()
        if not lines:
            return "", 1, 1
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if end < start:
            end = start
        return "\n".join(lines[start - 1 : end]), start, end

    @staticmethod
    def _trim_to_tokens(content: str, *, max_tokens: int) -> tuple[str, bool]:
        max_chars = max_tokens * APPROX_CHARS_PER_TOKEN
        if len(content) <= max_chars:
            return content, False
        return content[:max_chars].rstrip(), True

    @staticmethod
    def _bounded_int(
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _normalized_scope(values: list[str] | None) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            candidate = value.strip().strip("/")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _quote_path(value: str) -> str:
        return quote(value.strip().strip("/"), safe="")

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @staticmethod
    def _search_query(*, query: str, path_filter: str | None) -> str:
        normalized_query = " ".join(query.split())
        if not isinstance(path_filter, str):
            return normalized_query
        normalized_path = path_filter.strip().strip("/")
        if not normalized_path or "path:" in normalized_query.lower():
            return normalized_query
        return f"{normalized_query} path:{normalized_path}"

    @staticmethod
    def _next_page(headers: httpx.Headers) -> str | None:
        value = headers.get("x-next-page")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _compact_snippet(value: str | None) -> str | None:
        if value is None:
            return None
        compact = " ".join(value.split())
        if len(compact) <= 500:
            return compact
        return compact[:500].rstrip()

    @staticmethod
    def _file_url(
        *,
        base_url: str,
        project_path: str,
        ref: str,
        file_path: str,
    ) -> str:
        return (
            f"{base_url.rstrip('/')}/{project_path.strip().strip('/')}/-/blob/"
            f"{quote(ref, safe='')}/{file_path.strip().strip('/')}"
        )
