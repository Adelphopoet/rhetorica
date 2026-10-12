"""GitLab-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.gitlab import GitLabRequestError, GitLabService
from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)
from rhetorica_backend.infra.postgres.gitlab_connector_repository import (
    GitLabConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState


class ToolGitLabCapabilityService:
    """Capability-family service for read-only GitLab tools."""

    def __init__(
        self,
        *,
        gitlab_connector_repository: GitLabConnectorRepository | None,
        gitlab_service: GitLabService | None = None,
        bounded_int: Callable[..., int],
        tool_error_from_exception: Callable[..., dict[str, Any]],
    ) -> None:
        self._gitlab_connector_repository = gitlab_connector_repository
        self._gitlab_service = gitlab_service or GitLabService()
        self._bounded_int = bounded_int
        self._tool_error_from_exception = tool_error_from_exception

    def execute_search_gitlab_content(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="gitlab_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab search query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        limit = self._bounded_int(
            arguments.get("limit"),
            default=connection.search_limit,
            minimum=1,
            maximum=10,
        )
        try:
            results = retry_idempotent(
                lambda: self._gitlab_service.search_content(
                    connection,
                    query=query.strip(),
                    limit=limit,
                    project_paths=self._string_list(arguments.get("project_paths")),
                    group_paths=self._string_list(arguments.get("group_paths")),
                    ref=self._optional_str(arguments.get("ref")),
                    path_filter=self._optional_str(arguments.get("path_filter")),
                ),
                max_attempts=2,
            )
        except GitLabRequestError as exc:
            state.warnings.append("gitlab_search_failed")
            return self._gitlab_request_error_result(exc, default_error_code="gitlab_search_failed")
        except Exception as exc:
            state.warnings.append("gitlab_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="gitlab_search_failed",
                can_continue=True,
            )

        if results:
            state.metadata_sources.add("gitlab")
        return {
            "ok": True,
            "connector_name": connector_name,
            "count": len(results),
            "results": results,
            "usage_guidance": (
                "Search returned plausible file candidates. Next action: read exact project_path/file_path "
                "from these results. Do not keep searching variants or enumerate sibling directories."
                if results
                else "No matches for this exact search. Try only a materially different exact identifier or path_filter."
            ),
            "metadata_sources": sorted(state.metadata_sources),
        }

    def execute_find_gitlab_paths(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="gitlab_path_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab path query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        limit = self._bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=50)
        max_pages = self._bounded_int(arguments.get("max_pages"), default=10, minimum=1, maximum=50)
        try:
            path_payload = retry_idempotent(
                lambda: self._gitlab_service.find_paths(
                    connection,
                    query=query.strip(),
                    limit=limit,
                    project_paths=self._string_list(arguments.get("project_paths")),
                    ref=self._optional_str(arguments.get("ref")),
                    path=self._optional_str(arguments.get("path")),
                    recursive=arguments.get("recursive") is not False,
                    max_pages=max_pages,
                ),
                max_attempts=2,
            )
        except GitLabRequestError as exc:
            state.warnings.append("gitlab_path_search_failed")
            return self._gitlab_request_error_result(exc, default_error_code="gitlab_path_search_failed")
        except Exception as exc:
            state.warnings.append("gitlab_path_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="gitlab_path_search_failed",
                can_continue=True,
            )

        matches = path_payload.get("matches")
        if isinstance(matches, list) and matches:
            state.metadata_sources.add("gitlab")
        return {
            "ok": True,
            "connector_name": connector_name,
            "count": len(matches) if isinstance(matches, list) else 0,
            "results": matches if isinstance(matches, list) else [],
            "search": {
                key: value
                for key, value in path_payload.items()
                if key not in {"matches"}
            },
            "usage_guidance": (
                "Use returned path values with list_gitlab_tree for directories or read_gitlab_file for files. "
                "If truncated is true, continue with next_page or a narrower path."
            ),
            "metadata_sources": sorted(state.metadata_sources),
        }

    def execute_read_gitlab_file(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error

        project_path = arguments.get("project_path") or arguments.get("project")
        file_path = arguments.get("file_path")
        if not isinstance(project_path, str) or not project_path.strip():
            return build_tool_error(
                error_code="gitlab_project_path_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab project_path is required.",
                retry_recommended=False,
                can_continue=True,
            )
        if not isinstance(file_path, str) or not file_path.strip():
            return build_tool_error(
                error_code="gitlab_file_path_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab file_path is required.",
                retry_recommended=False,
                can_continue=True,
            )

        max_tokens = self._bounded_int(
            arguments.get("max_tokens"),
            default=connection.read_max_tokens,
            minimum=200,
            maximum=4000,
        )
        try:
            file_payload = retry_idempotent(
                lambda: self._gitlab_service.read_file(
                    connection,
                    project_path=project_path.strip().strip("/"),
                    file_path=file_path.strip().strip("/"),
                    ref=self._optional_str(arguments.get("ref")),
                    start_line=self._optional_positive_int(arguments.get("start_line")),
                    end_line=self._optional_positive_int(arguments.get("end_line")),
                    max_tokens=max_tokens,
                ),
                max_attempts=2,
            )
        except GitLabRequestError as exc:
            state.warnings.append("gitlab_read_failed")
            return self._gitlab_request_error_result(exc, default_error_code="gitlab_read_failed")
        except Exception as exc:
            state.warnings.append("gitlab_read_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="gitlab_read_failed",
                can_continue=True,
            )

        state.metadata_sources.add("gitlab")
        return {
            "ok": True,
            "connector_name": connector_name,
            "file": file_payload,
            "usage_guidance": (
                "Use this file as GitLab evidence. If it identifies the target DAG/config/SQL, answer now; "
                "do not reread the same file with wider ranges or keep exploring the repository."
            ),
            "metadata_sources": sorted(state.metadata_sources),
        }

    def execute_list_gitlab_tree(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error

        project_path = arguments.get("project_path") or arguments.get("project")
        if not isinstance(project_path, str) or not project_path.strip():
            return build_tool_error(
                error_code="gitlab_project_path_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab project_path is required.",
                retry_recommended=False,
                can_continue=True,
            )

        limit = self._bounded_int(
            arguments.get("limit"),
            default=50,
            minimum=1,
            maximum=100,
        )
        page = self._bounded_int(
            arguments.get("page"),
            default=1,
            minimum=1,
            maximum=1000,
        )
        try:
            tree_payload = retry_idempotent(
                lambda: self._gitlab_service.list_tree(
                    connection,
                    project_path=project_path.strip().strip("/"),
                    path=self._optional_str(arguments.get("path")),
                    ref=self._optional_str(arguments.get("ref")),
                    recursive=arguments.get("recursive") is True,
                    limit=limit,
                    page=page,
                ),
                max_attempts=2,
            )
        except GitLabRequestError as exc:
            state.warnings.append("gitlab_tree_failed")
            return self._gitlab_request_error_result(exc, default_error_code="gitlab_tree_failed")
        except Exception as exc:
            state.warnings.append("gitlab_tree_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="gitlab_tree_failed",
                can_continue=True,
            )

        state.metadata_sources.add("gitlab")
        items = tree_payload.get("items")
        return {
            "ok": True,
            "connector_name": connector_name,
            "count": len(items) if isinstance(items, list) else 0,
            "items": items if isinstance(items, list) else [],
            "tree": {
                key: value
                for key, value in tree_payload.items()
                if key not in {"items"}
            },
            "usage_guidance": (
                "Use this bounded directory listing to choose exact file_path values for read_gitlab_file. "
                "If truncated is true, call list_gitlab_tree with page=next_page for the same path."
            ),
            "metadata_sources": sorted(state.metadata_sources),
        }

    def execute_get_gitlab_file_blame(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection, connector_name, error = self._resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if error is not None:
            return error

        project_path = arguments.get("project_path") or arguments.get("project")
        file_path = arguments.get("file_path")
        start_line = self._optional_positive_int(arguments.get("start_line"))
        end_line = self._optional_positive_int(arguments.get("end_line"))
        if not isinstance(project_path, str) or not project_path.strip():
            return build_tool_error(
                error_code="gitlab_project_path_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab project_path is required.",
                retry_recommended=False,
                can_continue=True,
            )
        if not isinstance(file_path, str) or not file_path.strip():
            return build_tool_error(
                error_code="gitlab_file_path_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab file_path is required.",
                retry_recommended=False,
                can_continue=True,
            )
        if start_line is None or end_line is None:
            return build_tool_error(
                error_code="gitlab_blame_line_range_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab blame requires start_line and end_line.",
                retry_recommended=False,
                can_continue=True,
            )

        try:
            blame_payload = retry_idempotent(
                lambda: self._gitlab_service.get_file_blame(
                    connection,
                    project_path=project_path.strip().strip("/"),
                    file_path=file_path.strip().strip("/"),
                    ref=self._optional_str(arguments.get("ref")),
                    start_line=start_line,
                    end_line=end_line,
                ),
                max_attempts=2,
            )
        except GitLabRequestError as exc:
            state.warnings.append("gitlab_blame_failed")
            return self._gitlab_request_error_result(exc, default_error_code="gitlab_blame_failed")
        except Exception as exc:
            state.warnings.append("gitlab_blame_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="gitlab_blame_failed",
                can_continue=True,
            )

        state.metadata_sources.add("gitlab")
        ranges = blame_payload.get("ranges")
        return {
            "ok": True,
            "connector_name": connector_name,
            "count": len(ranges) if isinstance(ranges, list) else 0,
            "blame": blame_payload,
            "usage_guidance": (
                "Use blame only for ownership/change-history questions. For implementation details, "
                "prefer read_gitlab_file content evidence."
            ),
            "metadata_sources": sorted(state.metadata_sources),
        }

    def _resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        connector_name: Any,
        state: ToolOrchestrationState,
    ) -> tuple[Any, str | None, dict[str, Any] | None]:
        if self._gitlab_connector_repository is None:
            state.warnings.append("gitlab_not_configured")
            return None, None, build_tool_error(
                error_code="gitlab_not_configured",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab integration is not configured.",
                retry_recommended=False,
                can_continue=True,
            )
        decision = AgentConnectorPolicy.resolve_connector_name(
            requested_name=connector_name,
            allowed_names=request.agent_allowed_gitlab_connector_names,
            family="gitlab",
        )
        if not decision.allowed:
            if decision.warning:
                state.warnings.append(decision.warning)
            return None, None, build_tool_error(
                error_code=decision.warning or "agent_gitlab_connector_denied",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab connector is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )
        connection = self._gitlab_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=decision.connector_name,
        )
        if connection is None:
            state.warnings.append("gitlab_connector_missing")
            return None, decision.connector_name, build_tool_error(
                error_code="gitlab_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="GitLab connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )
        return connection, decision.connector_name, None

    @staticmethod
    def _gitlab_request_error_result(
        exc: GitLabRequestError,
        *,
        default_error_code: str,
    ) -> dict[str, Any]:
        retry_recommended = exc.status_code in {408, 429} or exc.status_code >= 500
        user_message = (
            "GitLab project, path, or file was not found. Check project_path/file_path from search results."
            if exc.status_code == 404
            else f"GitLab API request failed with HTTP {exc.status_code}."
        )
        payload = build_tool_error(
            error_code=default_error_code,
            error_category=ErrorCategory.RECOVERABLE,
            user_message=user_message,
            retry_recommended=retry_recommended,
            can_continue=True,
        )
        payload["gitlab_error"] = exc.to_payload()
        return payload

    @staticmethod
    def _string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        items = value if isinstance(value, list) else [value]
        normalized = [item.strip().strip("/") for item in items if isinstance(item, str)]
        return [item for item in normalized if item]

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _optional_positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
