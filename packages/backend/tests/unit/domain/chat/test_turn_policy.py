"""Tests for orchestration turn policy."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from rhetorica_backend.domain.chat.orchestration import shared_kernel
from rhetorica_backend.domain.chat.orchestration.turn_policy import (
    is_explicit_sql_inspection_request,
    resolve_turn_policy,
)


def _request(question: str, run_sql: bool = True):
    return SimpleNamespace(question=question, run_sql=run_sql)


def test_resolve_turn_policy_prefers_sql_generation_when_run_sql_available() -> None:
    policy = resolve_turn_policy(
        request=_request("Покажи выручку по дням"),
        has_db_connectors=True,
        metadata_configured=True,
        rag_configured=True,
        confluence_configured=True,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "sql_generation"
    assert policy.run_sql_tools_enabled is True
    assert policy.tools_available is True


def test_resolve_turn_policy_switches_to_discovery_for_definition_lookup() -> None:
    policy = resolve_turn_policy(
        request=_request("Что такое dds_orders_daily?"),
        has_db_connectors=True,
        metadata_configured=True,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=True,
    )

    assert policy.prompt_mode == "discovery"
    assert policy.run_sql_tools_enabled is False
    assert policy.metadata_tools_enabled is True


def test_resolve_turn_policy_uses_direct_response_without_tools() -> None:
    policy = resolve_turn_policy(
        request=_request("Привет", run_sql=False),
        has_db_connectors=False,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "direct_response"
    assert policy.tools_available is False


def test_explicit_sql_inspection_request_detection_handles_describe_and_show_create() -> None:
    assert is_explicit_sql_inspection_request("describe dm.ft_order") is True
    assert (
        is_explicit_sql_inspection_request("попробуй запустить describe dm.ft_order")
        is True
    )
    assert is_explicit_sql_inspection_request("show create table dm.ft_order") is True
    assert is_explicit_sql_inspection_request("Что такое dm.ft_order?") is False


def test_resolve_turn_policy_keeps_run_sql_enabled_for_explicit_describe_request() -> None:
    policy = resolve_turn_policy(
        request=_request("попробуй запустить describe dm.ft_order"),
        has_db_connectors=True,
        metadata_configured=True,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=True,
    )

    assert policy.prompt_mode == "sql_generation"
    assert policy.run_sql_tools_enabled is True
    assert policy.requires_metadata_lookup is False


def test_requires_metadata_lookup_ignores_explicit_sql_inspection_requests() -> None:
    request = _request("попробуй запустить describe dm.ft_order")

    assert shared_kernel.requires_metadata_lookup(request) is False


def test_resolve_turn_policy_keeps_jira_read_tools_for_non_analytic_ticket_lookup() -> None:
    mock_router = MagicMock()
    mock_router.is_non_analytic_question.return_value = True

    policy = resolve_turn_policy(
        request=_request("ABC-123"),
        has_db_connectors=False,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=True,
        sql_router=mock_router,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "discovery"
    assert policy.jira_read_tools_enabled is True
    assert policy.jira_write_tools_enabled is True


def test_resolve_turn_policy_suppresses_jira_tools_for_plain_greeting() -> None:
    mock_router = MagicMock()
    mock_router.is_non_analytic_question.return_value = True

    policy = resolve_turn_policy(
        request=_request("привет", run_sql=False),
        has_db_connectors=False,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=True,
        sql_router=mock_router,
        requires_metadata_lookup=False,
    )

    assert policy.prompt_mode == "direct_response"
    assert policy.jira_read_tools_enabled is False
    assert policy.jira_write_tools_enabled is False
    assert policy.tools_available is False


def test_resolve_turn_policy_exposes_jira_write_tools_for_create_request() -> None:
    policy = resolve_turn_policy(
        request=_request("заведи jira ticket на эту проблему", run_sql=False),
        has_db_connectors=False,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=True,
        sql_router=None,
        requires_metadata_lookup=False,
    )

    assert policy.jira_read_tools_enabled is True
    assert policy.jira_write_tools_enabled is True


def test_profile_tools_enabled_with_db_connectors_and_analytic_question() -> None:
    """profile_tools_enabled should be True when DB connectors exist and question is analytic."""
    policy = resolve_turn_policy(
        request=_request("Покажи выручку по дням"),
        has_db_connectors=True,
        metadata_configured=True,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=False,
    )
    assert policy.profile_tools_enabled is True


def test_profile_tools_disabled_without_db_connectors() -> None:
    """profile_tools_enabled should be False when no DB connectors."""
    policy = resolve_turn_policy(
        request=_request("Привет"),
        has_db_connectors=False,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=False,
        sql_router=None,
        requires_metadata_lookup=False,
    )
    assert policy.profile_tools_enabled is False


def test_profile_tools_disabled_for_non_analytic_question() -> None:
    """profile_tools_enabled should be False when question is non-analytic."""
    mock_router = MagicMock()
    mock_router.is_non_analytic_question.return_value = True
    policy = resolve_turn_policy(
        request=_request("ABC-123"),
        has_db_connectors=True,
        metadata_configured=False,
        rag_configured=False,
        confluence_configured=False,
        jira_configured=True,
        sql_router=mock_router,
        requires_metadata_lookup=False,
    )
    assert policy.profile_tools_enabled is False
