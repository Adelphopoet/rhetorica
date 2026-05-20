from __future__ import annotations

from rhetorica_backend.domain.chat.orchestration.jira_tool_support_services import (
    JiraConnectionService,
    JiraIssueDraftService,
)


def test_jira_connection_service_build_search_jql_escapes_quotes() -> None:
    jql = JiraConnectionService.build_search_jql(
        query='bad "quote"',
        project_key=None,
        assignee="analyst",
        status=None,
        issue_type=None,
        allowed_projects=["DATA"],
    )

    assert 'summary ~ "bad \\"quote\\""' in jql
    assert 'project in ("DATA")' in jql
    assert 'assignee = "analyst"' in jql


def test_jira_issue_draft_service_selects_russian_task_issue_type() -> None:
    issue_type = JiraIssueDraftService.select_issue_type(
        create_meta={
            "projects": [
                {
                    "key": "DWH",
                    "issuetypes": [
                        {"id": "10001", "name": "История", "fields": {}},
                        {"id": "10002", "name": "Задача", "fields": {}},
                    ],
                }
            ]
        },
        project_key="DWH",
        request_kind="task",
    )

    assert issue_type == {"id": "10002", "name": "Задача", "fields": {}}


def test_jira_issue_draft_service_blocker_stops_tool_loop() -> None:
    result = JiraIssueDraftService.blocker(
        error_code="jira_project_unclear",
        user_message="Нужен project.",
    )

    assert result["ok"] is False
    assert result["needs_user_input"] is True
    assert result["can_continue"] is False
