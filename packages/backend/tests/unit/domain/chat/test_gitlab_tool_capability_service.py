from __future__ import annotations

from typing import Any

from rhetorica_backend.contracts.admin_integrations import GitLabConnectionConfig
from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.gitlab import GitLabRequestError
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_gitlab_capability_service import (
    ToolGitLabCapabilityService,
)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _error_from_exception(_exc: Exception, *, default_error_code: str, can_continue: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": default_error_code,
        "can_continue": can_continue,
    }


class _Repository:
    def __init__(self, connection: GitLabConnectionConfig | None) -> None:
        self.connection = connection
        self.requested_name: str | None = None

    def get_connection(self, *, workspace_id: str, name: str | None = None) -> GitLabConnectionConfig | None:
        assert workspace_id == "default"
        self.requested_name = name
        return self.connection


class _GitLabService:
    def search_content(self, _connection: GitLabConnectionConfig, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["limit"] == 10
        assert kwargs["path_filter"] == "src/jobs"
        return [{"project_path": "group/app", "file_path": "README.md", "snippet": "compact"}]

    def find_paths(self, _connection: GitLabConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] == 50
        assert kwargs["max_pages"] == 50
        assert kwargs["path"] == "src"
        return {
            "query": kwargs["query"],
            "path": kwargs["path"],
            "ref": "HEAD",
            "recursive": True,
            "matches": [{"project_path": "group/app", "path": "src/jobs", "type": "tree"}],
            "scanned_items": 12,
            "scanned_pages": 2,
            "truncated": False,
            "next_page": None,
        }

    def read_file(self, _connection: GitLabConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["max_tokens"] == 4000
        return {"project_path": kwargs["project_path"], "file_path": kwargs["file_path"], "content": "bounded"}

    def list_tree(self, _connection: GitLabConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["limit"] == 100
        assert kwargs["page"] == 2
        assert kwargs["path"] == "src/jobs"
        return {
            "project_path": kwargs["project_path"],
            "path": kwargs["path"],
            "ref": "HEAD",
            "page": kwargs["page"],
            "next_page": "3",
            "truncated": True,
            "items": [{"project_path": kwargs["project_path"], "path": "src/jobs/dag.py", "file_path": "src/jobs/dag.py"}],
        }

    def get_file_blame(self, _connection: GitLabConnectionConfig, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["start_line"] == 10
        assert kwargs["end_line"] == 12
        return {
            "project_path": kwargs["project_path"],
            "file_path": kwargs["file_path"],
            "start_line": kwargs["start_line"],
            "end_line": kwargs["end_line"],
            "ranges": [{"start_line": 10, "end_line": 12, "author_name": "Ada"}],
        }


class _GitLabNotFoundService(_GitLabService):
    def find_paths(self, _connection: GitLabConnectionConfig, **_kwargs: Any) -> dict[str, Any]:
        raise GitLabRequestError(
            status_code=404,
            request_path="/api/v4/projects/group%2Fmissing/repository/tree",
            request_params={"ref": "HEAD"},
            response_body='{"message":"404 Project Not Found"}',
        )


def _connection() -> GitLabConnectionConfig:
    return GitLabConnectionConfig(
        provider="gitlab",
        base_url="https://gitlab.example.com",
        api_token="secret-token",
        use_when="Use for source lookup.",
        search_limit=5,
        read_max_tokens=1200,
    )


def _service(repository: _Repository | None) -> ToolGitLabCapabilityService:
    return ToolGitLabCapabilityService(
        gitlab_connector_repository=repository,  # type: ignore[arg-type]
        gitlab_service=_GitLabService(),  # type: ignore[arg-type]
        bounded_int=_bounded_int,
        tool_error_from_exception=_error_from_exception,
    )


def _service_with_gitlab(
    repository: _Repository | None,
    gitlab_service: Any,
) -> ToolGitLabCapabilityService:
    return ToolGitLabCapabilityService(
        gitlab_connector_repository=repository,  # type: ignore[arg-type]
        gitlab_service=gitlab_service,
        bounded_int=_bounded_int,
        tool_error_from_exception=_error_from_exception,
    )


def test_search_gitlab_content_respects_allowlist_and_trims_limit() -> None:
    repository = _Repository(_connection())
    request = ChatAskRequest(
        question="search repo",
        agent_allowed_gitlab_connector_names=["gitlab-main"],
    )
    state = ToolOrchestrationState()

    result = _service(repository).execute_search_gitlab_content(
        request=request,
        arguments={"connector_name": "gitlab-main", "query": "auth", "limit": 50, "path_filter": "src/jobs"},
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert "read exact project_path/file_path" in result["usage_guidance"]
    assert "enumerate sibling directories" in result["usage_guidance"]
    assert result["metadata_sources"] == ["gitlab"]
    assert state.metadata_sources == {"gitlab"}
    assert repository.requested_name == "gitlab-main"


def test_search_gitlab_content_rejects_connector_outside_agent_allowlist() -> None:
    request = ChatAskRequest(
        question="search repo",
        agent_allowed_gitlab_connector_names=["gitlab-main"],
    )
    state = ToolOrchestrationState()

    result = _service(_Repository(_connection())).execute_search_gitlab_content(
        request=request,
        arguments={"connector_name": "gitlab-dev", "query": "auth"},
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "agent_gitlab_connector_not_allowed"
    assert state.warnings == ["agent_gitlab_connector_not_allowed"]


def test_read_gitlab_file_bounds_max_tokens() -> None:
    request = ChatAskRequest(question="read repo")
    state = ToolOrchestrationState()

    result = _service(_Repository(_connection())).execute_read_gitlab_file(
        request=request,
        arguments={"project_path": "group/app", "file_path": "README.md", "max_tokens": 99999},
        state=state,
    )

    assert result["ok"] is True
    assert result["file"]["content"] == "bounded"
    assert "answer now" in result["usage_guidance"]
    assert result["metadata_sources"] == ["gitlab"]


def test_find_gitlab_paths_bounds_scan_and_returns_matches() -> None:
    request = ChatAskRequest(question="find repo path")
    state = ToolOrchestrationState()

    result = _service(_Repository(_connection())).execute_find_gitlab_paths(
        request=request,
        arguments={"query": "jobs", "project_paths": ["group/app"], "path": "src", "limit": 999, "max_pages": 999},
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["results"][0]["path"] == "src/jobs"
    assert result["search"]["scanned_pages"] == 2
    assert "list_gitlab_tree" in result["usage_guidance"]
    assert result["metadata_sources"] == ["gitlab"]


def test_gitlab_not_found_errors_are_not_retry_recommended() -> None:
    request = ChatAskRequest(question="find repo path")
    state = ToolOrchestrationState()

    result = _service_with_gitlab(
        _Repository(_connection()),
        _GitLabNotFoundService(),
    ).execute_find_gitlab_paths(
        request=request,
        arguments={"query": "dm2_main.py", "project_paths": ["group/missing"]},
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "gitlab_path_search_failed"
    assert result["retry_recommended"] is False
    assert "project, path, or file was not found" in result["user_message"]


def test_read_gitlab_file_requires_configured_connector() -> None:
    request = ChatAskRequest(question="read repo")
    state = ToolOrchestrationState()

    result = _service(None).execute_read_gitlab_file(
        request=request,
        arguments={"project_path": "group/app", "file_path": "README.md"},
        state=state,
    )

    assert result["ok"] is False
    assert result["error_code"] == "gitlab_not_configured"
    assert state.warnings == ["gitlab_not_configured"]


def test_list_gitlab_tree_bounds_limit_and_returns_items() -> None:
    request = ChatAskRequest(question="list repo")
    state = ToolOrchestrationState()

    result = _service(_Repository(_connection())).execute_list_gitlab_tree(
        request=request,
        arguments={"project_path": "group/app", "path": "src/jobs", "limit": 999, "page": 2},
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["items"][0]["file_path"] == "src/jobs/dag.py"
    assert result["tree"]["truncated"] is True
    assert result["tree"]["next_page"] == "3"
    assert "choose exact file_path" in result["usage_guidance"]
    assert result["metadata_sources"] == ["gitlab"]


def test_get_gitlab_file_blame_requires_line_range_and_returns_ranges() -> None:
    request = ChatAskRequest(question="who changed this line")
    state = ToolOrchestrationState()

    missing = _service(_Repository(_connection())).execute_get_gitlab_file_blame(
        request=request,
        arguments={"project_path": "group/app", "file_path": "src/jobs/dag.py"},
        state=state,
    )

    assert missing["ok"] is False
    assert missing["error_code"] == "gitlab_blame_line_range_missing"

    result = _service(_Repository(_connection())).execute_get_gitlab_file_blame(
        request=request,
        arguments={
            "project_path": "group/app",
            "file_path": "src/jobs/dag.py",
            "start_line": 10,
            "end_line": 12,
        },
        state=state,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["blame"]["ranges"][0]["author_name"] == "Ada"
    assert "ownership" in result["usage_guidance"]
