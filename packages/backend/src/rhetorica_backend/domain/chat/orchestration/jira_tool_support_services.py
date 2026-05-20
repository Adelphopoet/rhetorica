"""Focused Jira support services for tool orchestration."""

from __future__ import annotations

import logging
from typing import Any

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.error_contract import (
    ErrorCategory,
    build_tool_error,
    retry_idempotent,
)
from rhetorica_backend.domain.chat.jira.service import JiraService

logger = logging.getLogger(__name__)


class JiraConnectionService:
    """Resolves Jira connections and search parameters."""

    def __init__(
        self,
        *,
        jira_connector_repository: Any | None,
    ) -> None:
        self._jira_connector_repository = jira_connector_repository

    def resolve_connection(
        self,
        *,
        request: ChatAskRequest,
        connector_name: Any,
        state: Any,
    ) -> Any | None:
        if self._jira_connector_repository is None:
            state.warnings.append("jira_not_configured")
            return None
        requested_name = connector_name if isinstance(connector_name, str) else None
        connection = self._jira_connector_repository.get_connection(
            workspace_id=request.workspace_id,
            name=requested_name,
        )
        if connection is not None:
            return connection
        return self._jira_connector_repository.get_connection(
            workspace_id=request.workspace_id
        )

    @staticmethod
    def build_search_jql(
        *,
        query: str,
        project_key: Any,
        assignee: Any,
        status: Any,
        issue_type: Any,
        allowed_projects: list[str],
    ) -> str:
        clauses: list[str] = []
        normalized_query = query.strip()
        if JiraService.is_issue_key(normalized_query):
            clauses.append(f'key = "{normalized_query.upper()}"')
        else:
            escaped = _escape_jql_value(normalized_query)
            clauses.append(f'(summary ~ "{escaped}" OR description ~ "{escaped}")')

        normalized_project = self_normalized_project_key(project_key)
        if normalized_project:
            clauses.append(f'project = "{normalized_project}"')
        elif allowed_projects:
            allowed = ", ".join(f'"{item}"' for item in allowed_projects)
            clauses.append(f"project in ({allowed})")

        for field_name, raw_value in (
            ("assignee", assignee),
            ("status", status),
            ("issuetype", issue_type),
        ):
            if isinstance(raw_value, str) and raw_value.strip():
                clauses.append(
                    f'{field_name} = "{_escape_jql_value(raw_value.strip())}"'
                )

        return " AND ".join(clauses)

    @staticmethod
    def bounded_int(
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int | float):
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return default
            return max(minimum, min(parsed, maximum))
        return default


def self_normalized_project_key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def _escape_jql_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class JiraRoutingService:
    """Resolves Jira assignee and project routing from metadata and Jira history."""

    def __init__(
        self,
        *,
        jira_service: JiraService,
        metadata_catalog_search_service: Any | None,
        metadata_connection_resolver: Any,
    ) -> None:
        self._jira_service = jira_service
        self._metadata_catalog_search_service = metadata_catalog_search_service
        self._metadata_connection_resolver = metadata_connection_resolver

    def resolve_assignee(
        self,
        *,
        connection: Any,
        project_key: str | None,
        hint: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(hint, str) or not hint.strip():
            return None
        normalized_hint = hint.strip()
        try:
            users = (
                self._jira_service.search_assignable_users(
                    connection,
                    project_key=project_key,
                    query=normalized_hint,
                    limit=10,
                )
                if project_key
                else self._jira_service.search_users(
                    connection,
                    query=normalized_hint,
                    limit=10,
                )
            )
        except Exception:
            return None
        return self._pick_unique_jira_user(users, normalized_hint)

    def derive_routing(
        self,
        *,
        request: ChatAskRequest,
        state: Any,
        connection: Any,
        context_fqn: str | None,
    ) -> dict[str, Any]:
        candidate_hints, notes = self._resolve_owner_hints(
            request=request,
            state=state,
            context_fqn=context_fqn,
        )
        matched_users: list[dict[str, Any]] = []
        for hint in candidate_hints:
            assignee = self.resolve_assignee(
                connection=connection,
                project_key=None,
                hint=hint,
            )
            if assignee is None:
                continue
            if not any(item.get("name") == assignee.get("name") for item in matched_users):
                matched_users.append(assignee)

        if len(matched_users) != 1:
            return {"notes": notes}

        assignee = matched_users[0]
        assignee_name = assignee.get("name")
        if not isinstance(assignee_name, str) or not assignee_name.strip():
            return {"notes": notes}

        history_jql = (
            f'assignee = "{_escape_jql_value(assignee_name)}" ORDER BY updated DESC'
        )
        if connection.project_keys:
            allowed = ", ".join(f'"{item}"' for item in connection.project_keys)
            history_jql = (
                f'project in ({allowed}) AND assignee = "{_escape_jql_value(assignee_name)}" '
                "ORDER BY updated DESC"
            )
        try:
            issues = self._jira_service.search_issues(
                connection,
                jql=history_jql,
                limit=20,
            )
        except Exception:
            issues = []

        project_key = self._pick_leading_jira_project(issues)
        if project_key:
            notes.append(
                "Routing inferred from recent Jira issues assigned to "
                f"{assignee.get('display_name') or assignee_name}."
            )
        return {
            "project_key": project_key,
            "assignee": assignee,
            "notes": notes,
        }

    def _resolve_owner_hints(
        self,
        *,
        request: ChatAskRequest,
        state: Any,
        context_fqn: str | None,
    ) -> tuple[list[str], list[str]]:
        hints: list[str] = []
        notes: list[str] = []
        if context_fqn:
            notes.append(f"Source entity: {context_fqn}")
        for owner in self._owner_strings_from_state(state=state, context_fqn=context_fqn):
            if owner not in hints:
                hints.append(owner)

        metadata_connection = self._metadata_connection_resolver(
            request=request,
            arguments={},
            warnings=state.warnings,
            db_connectors=[],
        )
        if (
            metadata_connection is None
            or self._metadata_catalog_search_service is None
            or not getattr(metadata_connection, "mcp_tables_url", None)
        ):
            return hints, notes

        if context_fqn:
            try:
                entity_payload, _ = self._metadata_catalog_search_service.get_entity_details(
                    connection=metadata_connection,
                    entity_type="table",
                    fqn=context_fqn,
                )
            except Exception:
                entity_payload = None
            entity = (
                entity_payload.get("entity")
                if isinstance(entity_payload, dict)
                else None
            )
            if isinstance(entity, dict):
                for owner in _list_of_strings(entity.get("owners")):
                    if owner not in hints:
                        hints.append(owner)

        for owner in list(hints):
            for entity_type in ("user", "team"):
                try:
                    results, _ = self._metadata_catalog_search_service.search_catalog(
                        connection=metadata_connection,
                        query=owner,
                        entity_type=entity_type,
                        limit=5,
                    )
                except Exception:
                    logger.debug("Metadata catalog search failed while resolving Jira owner", exc_info=True)
                    continue
                for item in results:
                    fqn = (
                        item.get("fullyQualifiedName")
                        or item.get("fqn")
                        or item.get("name")
                    )
                    if not isinstance(fqn, str) or not fqn.strip():
                        continue
                    try:
                        entity_payload, _ = self._metadata_catalog_search_service.get_entity_details(
                            connection=metadata_connection,
                            entity_type=entity_type,
                            fqn=fqn.strip(),
                        )
                    except Exception:
                        logger.debug(
                            "Metadata entity lookup failed while resolving Jira owner",
                            exc_info=True,
                        )
                        continue
                    entity = (
                        entity_payload.get("entity")
                        if isinstance(entity_payload, dict)
                        else None
                    )
                    for hint in self._human_hints_from_metadata_entity(
                        entity=entity,
                        entity_type=entity_type,
                    ):
                        if hint not in hints:
                            hints.append(hint)

        return hints, notes

    @staticmethod
    def _owner_strings_from_state(
        *,
        state: Any,
        context_fqn: str | None,
    ) -> list[str]:
        if state.semantic_grounding is None:
            return []
        owners: list[str] = []
        for candidate in state.semantic_grounding.candidates:
            if context_fqn and candidate.fqn != context_fqn:
                continue
            for owner in candidate.owners:
                clean = owner.strip()
                if clean and clean not in owners:
                    owners.append(clean)
        return owners

    @classmethod
    def _human_hints_from_metadata_entity(
        cls,
        *,
        entity: Any,
        entity_type: str,
    ) -> list[str]:
        if not isinstance(entity, dict):
            return []
        hints: list[str] = []
        if entity_type == "user":
            cls._extend_human_hints(
                hints,
                entity,
                keys=("displayName", "name", "email", "emailAddress", "fullyQualifiedName"),
            )
            return hints

        for field_name in (
            "owners",
            "users",
            "members",
            "defaultReviewer",
            "defaultAssignee",
        ):
            cls._collect_human_hints(entity.get(field_name), hints)
        return hints

    @classmethod
    def _collect_human_hints(cls, value: Any, target: list[str]) -> None:
        if isinstance(value, list):
            for item in value:
                cls._collect_human_hints(item, target)
            return
        if isinstance(value, dict):
            cls._extend_human_hints(
                target,
                value,
                keys=("displayName", "name", "email", "emailAddress", "fullyQualifiedName"),
            )
            for nested_key in ("user", "member", "owner"):
                cls._collect_human_hints(value.get(nested_key), target)

    @staticmethod
    def _extend_human_hints(
        target: list[str],
        value: dict[str, Any],
        keys: tuple[str, ...],
    ) -> None:
        for key in keys:
            candidate = value.get(key)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            clean = candidate.strip().split(".")[-1]
            if clean and clean not in target:
                target.append(clean)

    @staticmethod
    def _pick_unique_jira_user(
        users: list[dict[str, Any]],
        hint: str,
    ) -> dict[str, Any] | None:
        if len(users) == 1:
            return users[0]
        normalized_hint = hint.strip().lower()
        exact = [
            item
            for item in users
            if any(
                isinstance(item.get(field), str)
                and item.get(field, "").strip().lower() == normalized_hint
                for field in ("name", "key", "display_name", "email")
            )
        ]
        if len(exact) == 1:
            return exact[0]
        return None

    @staticmethod
    def _pick_leading_jira_project(issues: list[dict[str, Any]]) -> str | None:
        counts: dict[str, int] = {}
        for issue in issues:
            project = issue.get("project")
            if not isinstance(project, dict):
                continue
            key = project.get("key")
            if not isinstance(key, str) or not key.strip():
                continue
            normalized = key.strip().upper()
            counts[normalized] = counts.get(normalized, 0) + 1
        if not counts:
            return None
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            return ranked[0][0]
        return None


class JiraIssueDraftService:
    """Builds Jira issue drafts after routing is known."""

    def __init__(
        self,
        *,
        jira_service: JiraService,
    ) -> None:
        self._jira_service = jira_service

    def resolve_reporter(
        self,
        *,
        connection: Any,
        issue_type: dict[str, Any],
    ) -> dict[str, Any] | None:
        fields = issue_type.get("fields")
        if not isinstance(fields, dict):
            return None
        reporter_field = fields.get("reporter")
        if not isinstance(reporter_field, dict) or not reporter_field.get("required"):
            return None
        try:
            reporter = retry_idempotent(
                lambda: self._jira_service.get_myself(connection),
                max_attempts=3,
            )
        except Exception:
            return None
        return reporter if isinstance(reporter, dict) and reporter else None

    @staticmethod
    def issue_type_preferences(request_kind: str) -> tuple[str, ...]:
        if request_kind == "bug_report":
            return ("bug", "ошибка", "incident", "инцидент", "defect", "дефект", "task", "задача")
        if request_kind == "feature_request":
            return (
                "story",
                "история",
                "feature",
                "функция",
                "improvement",
                "улучшение",
                "task",
                "задача",
            )
        return ("task", "задача", "story", "история")

    @classmethod
    def select_issue_type(
        cls,
        *,
        create_meta: dict[str, Any],
        project_key: str,
        request_kind: str,
    ) -> dict[str, Any] | None:
        projects = create_meta.get("projects")
        if not isinstance(projects, list):
            return None
        for project in projects:
            if not isinstance(project, dict):
                continue
            if self_normalized_project_key(project.get("key")) != project_key:
                continue
            issue_types = project.get("issuetypes")
            if not isinstance(issue_types, list):
                return None
            for preferred in cls.issue_type_preferences(request_kind):
                for issue_type in issue_types:
                    if not isinstance(issue_type, dict):
                        continue
                    name = issue_type.get("name")
                    if (
                        isinstance(name, str)
                        and cls._normalized_issue_type_name(name) == preferred
                    ):
                        return issue_type
        return None

    @staticmethod
    def _normalized_issue_type_name(value: str) -> str:
        return " ".join(value.strip().lower().split())

    @staticmethod
    def missing_required_fields(
        issue_type: dict[str, Any],
        *,
        auto_field_ids: set[str] | None = None,
    ) -> list[str]:
        fields = issue_type.get("fields")
        if not isinstance(fields, dict):
            return []
        allowed_field_ids = {
            "summary",
            "description",
            "issuetype",
            "project",
            "assignee",
            *(auto_field_ids or set()),
        }
        missing: list[str] = []
        for field_id, field_meta in fields.items():
            if field_id in allowed_field_ids:
                continue
            if not isinstance(field_meta, dict) or not field_meta.get("required"):
                continue
            label = (
                field_meta.get("name")
                if isinstance(field_meta.get("name"), str)
                else field_id
            )
            if label not in missing:
                missing.append(label)
        return missing

    @staticmethod
    def build_description(
        *,
        request: ChatAskRequest,
        body: str,
        request_kind: str,
        context_fqn: str | None,
        project_key: str,
        assignee: dict[str, Any],
        routing_notes: list[str],
    ) -> str:
        expected_outcome = {
            "bug_report": "Investigate the problem, confirm impact, and fix or mitigate it.",
            "feature_request": "Evaluate the requested capability and define implementation scope.",
            "task": "Process the request and close the operational follow-up.",
        }.get(request_kind, "Process the request.")
        assignee_label = assignee.get("display_name") or assignee.get("name") or "unknown"
        notes = routing_notes or [
            "Routing derived from source ownership and recent Jira history."
        ]
        return "\n".join(
            [
                "Context",
                f"- User request: {request.question.strip()}",
                "",
                "Problem / Request",
                body,
                "",
                "Source entity",
                f"- {context_fqn or 'not resolved'}",
                "",
                "Expected outcome",
                f"- {expected_outcome}",
                "",
                "Routing rationale",
                f"- Project: {project_key}",
                f"- Assignee: {assignee_label}",
                *[f"- {note}" for note in notes],
            ]
        )

    @staticmethod
    def blocker(
        *,
        error_code: str,
        user_message: str,
    ) -> dict[str, Any]:
        result = build_tool_error(
            error_code=error_code,
            error_category=ErrorCategory.RECOVERABLE,
            user_message=user_message,
            retry_recommended=False,
            can_continue=False,
        )
        result["needs_user_input"] = True
        return result


def _nested_or_string_name(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("displayName", "name", "fullyQualifiedName"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().split(".")[-1]
    return None


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, dict):
            tag_fqn = item.get("tagFQN")
            if isinstance(tag_fqn, str) and tag_fqn.strip():
                items.append(tag_fqn.strip())
                continue
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
            continue
        nested = _nested_or_string_name(item)
        if nested:
            items.append(nested)
    return items
