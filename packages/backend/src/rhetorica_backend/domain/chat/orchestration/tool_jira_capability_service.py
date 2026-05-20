"""Jira-family orchestration tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.jira.service import JiraService
from rhetorica_backend.domain.chat.metadata import (
    MetadataCatalogSearchService,
    MetadataFetcher,
    resolve_metadata_runtime_services,
)
from rhetorica_backend.domain.chat.orchestration.jira_tool_support_services import (
    JiraConnectionService,
    JiraIssueDraftService,
    JiraRoutingService,
    self_normalized_project_key,
)
from rhetorica_backend.domain.chat.orchestration.metadata_tool_support_services import (
    MetadataToolConnectionService,
)
from rhetorica_backend.infra.postgres.jira_connector_repository import (
    JiraConnectorRepository,
)

if TYPE_CHECKING:
    from rhetorica_backend.domain.chat.orchestration.state import (
        ToolOrchestrationState,
    )


class ToolJiraCapabilityService:
    """Capability-family service for Jira tools."""

    def __init__(
        self,
        *,
        jira_connector_repository: JiraConnectorRepository | None = None,
        jira_service: JiraService | None = None,
        metadata_fetcher: MetadataFetcher | None = None,
        metadata_catalog_search_service: MetadataCatalogSearchService | None = None,
        metadata_connection_service: MetadataToolConnectionService,
        tool_error_from_exception: Callable[..., dict[str, Any]],
    ) -> None:
        self._jira_service = jira_service or JiraService()
        self._tool_error_from_exception = tool_error_from_exception
        self._metadata_catalog_search_service = resolve_metadata_runtime_services(
            metadata_fetcher=metadata_fetcher,
            metadata_catalog_search_service=metadata_catalog_search_service,
        ).catalog_search_service
        self._connection_service = JiraConnectionService(
            jira_connector_repository=jira_connector_repository,
        )
        self._routing_service = JiraRoutingService(
            jira_service=self._jira_service,
            metadata_catalog_search_service=self._metadata_catalog_search_service,
            metadata_connection_resolver=metadata_connection_service.resolve_connection,
        )
        self._issue_draft_service = JiraIssueDraftService(
            jira_service=self._jira_service,
        )

    def execute_search_jira_issues(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection = self._connection_service.resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if connection is None:
            return build_tool_error(
                error_code="jira_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Jira connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return build_tool_error(
                error_code="jira_query_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Jira search query is required.",
                retry_recommended=False,
                can_continue=True,
            )

        limit = self._connection_service.bounded_int(
            arguments.get("limit"),
            default=connection.issue_limit,
            minimum=1,
            maximum=20,
        )
        jql = self._connection_service.build_search_jql(
            query=query.strip(),
            project_key=arguments.get("project_key"),
            assignee=arguments.get("assignee"),
            status=arguments.get("status"),
            issue_type=arguments.get("issue_type"),
            allowed_projects=connection.project_keys,
        )

        try:
            issues = retry_idempotent(
                lambda: self._jira_service.search_issues(
                    connection,
                    jql=jql,
                    limit=limit,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("jira_search_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="jira_search_failed",
                can_continue=True,
            )

        return {
            "ok": True,
            "count": len(issues),
            "results": issues,
            "jql": jql,
        }

    def execute_get_jira_issue(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection = self._connection_service.resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if connection is None:
            return build_tool_error(
                error_code="jira_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Jira connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        issue_key = arguments.get("issue_key")
        if not isinstance(issue_key, str) or not JiraService.is_issue_key(issue_key):
            return build_tool_error(
                error_code="jira_issue_key_invalid",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="A valid Jira issue key is required.",
                retry_recommended=False,
                can_continue=True,
            )

        try:
            issue = retry_idempotent(
                lambda: self._jira_service.get_issue(
                    connection,
                    issue_key=issue_key.strip().upper(),
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("jira_issue_fetch_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="jira_issue_fetch_failed",
                can_continue=True,
            )

        return {"ok": True, "issue": issue}

    def execute_create_jira_issue(
        self,
        *,
        request: ChatAskRequest,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> dict[str, Any]:
        connection = self._connection_service.resolve_connection(
            request=request,
            connector_name=arguments.get("connector_name"),
            state=state,
        )
        if connection is None:
            return build_tool_error(
                error_code="jira_connector_missing",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Jira connector is missing.",
                retry_recommended=False,
                can_continue=True,
            )

        summary = arguments.get("summary")
        description = arguments.get("description")
        request_kind = arguments.get("request_kind")
        if not isinstance(summary, str) or not summary.strip():
            return self._issue_draft_service.blocker(
                error_code="jira_summary_missing",
                user_message="Jira summary is required.",
            )
        if not isinstance(description, str) or not description.strip():
            return self._issue_draft_service.blocker(
                error_code="jira_description_missing",
                user_message="Jira description is required.",
            )
        if request_kind not in {"bug_report", "feature_request", "task"}:
            return self._issue_draft_service.blocker(
                error_code="jira_request_kind_invalid",
                user_message="request_kind must be bug_report, feature_request, or task.",
            )

        project_key = self_normalized_project_key(arguments.get("project_key"))
        assignee = self._routing_service.resolve_assignee(
            connection=connection,
            project_key=project_key,
            hint=arguments.get("assignee_hint"),
        )
        routing_notes: list[str] = []
        context_fqn = self._resolve_jira_context_fqn(arguments=arguments, state=state)

        if project_key is None or assignee is None:
            routing = self._routing_service.derive_routing(
                request=request,
                state=state,
                connection=connection,
                context_fqn=context_fqn,
            )
            if project_key is None:
                project_key = routing.get("project_key")
            if assignee is None:
                assignee = routing.get("assignee")
            routing_notes.extend(self._list_of_strings(routing.get("notes")))

        if project_key is None:
            return self._issue_draft_service.blocker(
                error_code="jira_project_unclear",
                user_message="Не смог уверенно определить Jira project. Скажи, в какой project заводить задачу.",
            )
        if assignee is None:
            return self._issue_draft_service.blocker(
                error_code="jira_assignee_unclear",
                user_message="Не смог уверенно определить assignee. Подскажи, на кого вешать задачу.",
            )

        try:
            create_meta = retry_idempotent(
                lambda: self._jira_service.get_create_meta(
                    connection,
                    project_key=project_key,
                ),
                max_attempts=3,
            )
        except Exception as exc:
            state.warnings.append("jira_createmeta_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="jira_createmeta_failed",
                can_continue=False,
            )

        issue_type = self._issue_draft_service.select_issue_type(
            create_meta=create_meta,
            project_key=project_key,
            request_kind=str(request_kind),
        )
        if issue_type is None:
            return self._issue_draft_service.blocker(
                error_code="jira_issue_type_unavailable",
                user_message="В этом Jira project нет подходящего issue type для такого запроса.",
            )

        reporter = self._issue_draft_service.resolve_reporter(
            connection=connection,
            issue_type=issue_type,
        )
        missing_required_fields = self._issue_draft_service.missing_required_fields(
            issue_type,
            auto_field_ids={"reporter"} if reporter is not None else set(),
        )
        if missing_required_fields:
            return self._issue_draft_service.blocker(
                error_code="jira_required_fields_missing",
                user_message=(
                    "В этом Jira project обязательны дополнительные поля: "
                    + ", ".join(missing_required_fields)
                    + ". Уточни их вручную."
                ),
            )

        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary.strip(),
            "description": self._issue_draft_service.build_description(
                request=request,
                body=description.strip(),
                request_kind=str(request_kind),
                context_fqn=context_fqn,
                project_key=project_key,
                assignee=assignee,
                routing_notes=routing_notes,
            ),
            "issuetype": {"id": issue_type["id"]},
            "assignee": {"name": assignee.get("name") or assignee.get("key")},
        }
        if reporter is not None:
            fields["reporter"] = {"name": reporter.get("name") or reporter.get("key")}

        try:
            created = self._jira_service.create_issue(connection, fields=fields)
        except Exception as exc:
            state.warnings.append("jira_create_failed")
            return self._tool_error_from_exception(
                exc,
                default_error_code="jira_create_failed",
                can_continue=False,
            )

        return {
            "ok": True,
            "issue_key": created.get("key"),
            "url": created.get("url"),
            "project_key": project_key,
            "assignee": assignee,
            "issue_type": issue_type["name"],
        }

    @staticmethod
    def _resolve_jira_context_fqn(
        *,
        arguments: dict[str, Any],
        state: ToolOrchestrationState,
    ) -> str | None:
        context_fqn = arguments.get("context_fqn")
        if isinstance(context_fqn, str) and context_fqn.strip():
            return context_fqn.strip()
        if state.semantic_grounding and state.semantic_grounding.chosen_table_fqn:
            return state.semantic_grounding.chosen_table_fqn
        return None

    @classmethod
    def _list_of_strings(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
        return items
