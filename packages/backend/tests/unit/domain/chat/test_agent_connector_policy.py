from __future__ import annotations

from rhetorica_backend.domain.chat.orchestration.agent_connector_policy import (
    AgentConnectorPolicy,
)


def test_agent_connector_policy_requires_explicit_connector_when_multiple_are_allowed() -> None:
    decision = AgentConnectorPolicy.resolve_connector_name(
        requested_name=None,
        allowed_names=["alpha", "beta"],
        family="metadata",
    )

    assert decision.allowed is False
    assert decision.warning == "agent_metadata_connector_required"


def test_agent_connector_policy_selects_single_allowed_connector_deterministically() -> None:
    decision = AgentConnectorPolicy.resolve_connector_name(
        requested_name=None,
        allowed_names=["alpha"],
        family="metadata",
    )

    assert decision.allowed is True
    assert decision.connector_name == "alpha"
