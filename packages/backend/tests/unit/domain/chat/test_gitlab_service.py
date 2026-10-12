from __future__ import annotations

import httpx
import pytest
from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.domain.chat.gitlab import GitLabService


def _connection() -> GitLabConnectionConfig:
    return GitLabConnectionConfig(
        provider="gitlab",
        base_url="https://gitlab.example.com/",
        api_token="secret-token",
        use_when="Use for source lookup.",
        default_project_paths=["group/app"],
        default_group_paths=[],
        search_limit=5,
        read_max_tokens=500,
    )


def _service(handler) -> GitLabService:
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    return GitLabService(client_factory=client_factory)


def test_search_gitlab_content_returns_compact_refs_and_uses_private_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["PRIVATE-TOKEN"] == "secret-token"
        assert request.url.raw_path.startswith(b"/api/v4/projects/group%2Fapp/search")
        assert request.url.params["scope"] == "blobs"
        assert request.url.params["search"] == "analytics"
        return httpx.Response(
            200,
            json=[
                {
                    "path": "README.md",
                    "ref": "main",
                    "startline": 7,
                    "data": "  README\n\nUse this project for analytics.  ",
                    "project_id": 42,
                },
                {
                    "path": "src/app.py",
                    "ref": "main",
                    "startline": 10,
                    "data": "def run(): pass",
                    "project_id": 42,
                },
            ],
        )

    results = _service(handler).search_content(_connection(), query="analytics", limit=1)

    assert results == [
        {
            "project_path": "group/app",
            "project_id": 42,
            "group_path": None,
            "file_path": "README.md",
            "ref": "main",
            "startline": 7,
            "snippet": "README Use this project for analytics.",
            "url": "https://gitlab.example.com/group/app/-/blob/main/README.md",
        }
    ]


def test_search_gitlab_content_adds_path_filter_to_search_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == "dm_operations path:foodtech/operations/dm_operations"
        return httpx.Response(200, json=[])

    results = _service(handler).search_content(
        _connection(),
        query="dm_operations",
        path_filter=" /foodtech/operations/dm_operations/ ",
        limit=5,
    )

    assert results == []


def test_read_gitlab_file_supports_line_range_and_token_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(
            b"/api/v4/projects/group%2Fapp/repository/files/src%2Fapp.py/raw"
        )
        assert request.url.params["ref"] == "main"
        return httpx.Response(200, text="line1\nline2\nline3-is-long\nline4")

    payload = _service(handler).read_file(
        _connection(),
        project_path="group/app",
        file_path="src/app.py",
        ref="main",
        start_line=2,
        end_line=3,
        max_tokens=200,
    )

    assert payload["content"] == "line2\nline3-is-long"
    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert payload["truncated"] is False
    assert payload["url"] == "https://gitlab.example.com/group/app/-/blob/main/src/app.py"


def test_read_gitlab_file_caps_large_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 1200)

    payload = _service(handler).read_file(
        _connection(),
        project_path="group/app",
        file_path="big.txt",
        max_tokens=200,
    )

    assert len(payload["content"]) == 800
    assert payload["truncated"] is True


def test_read_gitlab_file_raises_for_auth_or_missing_file() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(Exception) as exc_info:
        _service(handler).read_file(
            _connection(),
            project_path="group/app",
            file_path="secret.py",
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.response_body == "unauthorized"


def test_list_gitlab_tree_returns_bounded_directory_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(b"/api/v4/projects/group%2Fapp/repository/tree")
        assert request.url.params["path"] == "src"
        assert request.url.params["ref"] == "main"
        assert request.url.params["per_page"] == "100"
        assert request.url.params["page"] == "1"
        return httpx.Response(
            200,
            headers={"X-Next-Page": "2"},
            json=[
                {
                    "id": "abc",
                    "name": "app.py",
                    "type": "blob",
                    "path": "src/app.py",
                    "mode": "100644",
                },
                {
                    "id": "def",
                    "name": "jobs",
                    "type": "tree",
                    "path": "src/jobs",
                    "mode": "040000",
                },
            ],
        )

    payload = _service(handler).list_tree(
        _connection(),
        project_path="group/app",
        path="/src/",
        ref="main",
        recursive=True,
        limit=999,
    )

    assert payload["path"] == "src"
    assert payload["recursive"] is True
    assert payload["limit"] == 100
    assert payload["page"] == 1
    assert payload["next_page"] == "2"
    assert payload["truncated"] is True
    assert payload["items"][0]["file_path"] == "src/app.py"
    assert payload["items"][0]["url"] == "https://gitlab.example.com/group/app/-/blob/main/src/app.py"
    assert payload["items"][1]["file_path"] is None
    assert payload["items"][1]["url"] == "https://gitlab.example.com/group/app/-/tree/main/src/jobs"


def test_find_gitlab_paths_paginates_and_matches_path_or_name() -> None:
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(b"/api/v4/projects/group%2Fapp/repository/tree")
        assert request.url.params["recursive"] == "true"
        assert request.url.params["path"] == "foodtech/operations"
        page = request.url.params["page"]
        seen_pages.append(page)
        if page == "1":
            return httpx.Response(
                200,
                headers={"X-Next-Page": "2"},
                json=[
                    {
                        "id": "abc",
                        "name": "checkout",
                        "type": "tree",
                        "path": "foodtech/operations/checkout",
                        "mode": "040000",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": "def",
                    "name": "dm_operations",
                    "type": "tree",
                    "path": "foodtech/operations/dm_operations",
                    "mode": "040000",
                }
            ],
        )

    payload = _service(handler).find_paths(
        _connection(),
        query="dm_operations",
        project_paths=["group/app"],
        path="foodtech/operations",
        limit=10,
        max_pages=5,
    )

    assert seen_pages == ["1", "2"]
    assert len(payload["matches"]) == 1
    assert payload["matches"][0]["path"] == "foodtech/operations/dm_operations"
    assert payload["matches"][0]["match_on"] == "path"
    assert payload["truncated"] is False


def test_get_gitlab_file_blame_returns_compact_commit_ranges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(
            b"/api/v4/projects/group%2Fapp/repository/files/src%2Fapp.py/blame"
        )
        assert request.url.params["range[start]"] == "10"
        assert request.url.params["range[end]"] == "12"
        return httpx.Response(
            200,
            json=[
                {
                    "commit": {
                        "id": "abc123",
                        "short_id": "abc123",
                        "title": "Add app",
                        "author_name": "Ada",
                        "author_email": "ada@example.com",
                        "authored_date": "2026-05-01T12:00:00Z",
                    },
                    "lines": ["def run():", "    return True"],
                },
                {
                    "commit": {
                        "id": "def456",
                        "short_id": "def456",
                        "title": "Wire app",
                    },
                    "lines": ["run()"],
                },
            ],
        )

    payload = _service(handler).get_file_blame(
        _connection(),
        project_path="group/app",
        file_path="src/app.py",
        ref="main",
        start_line=10,
        end_line=12,
    )

    assert payload["start_line"] == 10
    assert payload["end_line"] == 12
    assert payload["ranges"][0]["start_line"] == 10
    assert payload["ranges"][0]["end_line"] == 11
    assert payload["ranges"][0]["author_name"] == "Ada"
    assert payload["ranges"][1]["start_line"] == 12
    assert payload["ranges"][1]["title"] == "Wire app"
