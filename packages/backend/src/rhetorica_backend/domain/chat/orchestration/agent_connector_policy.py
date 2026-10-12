"""Agent connector allowlist enforcement for runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConnectorAccessDecision:
    """Result of resolving a connector name under an optional agent allowlist."""

    connector_name: str | None
    allowed: bool
    warning: str | None = None


class AgentConnectorPolicy:
    """Deterministic connector access policy for agent-scoped tool calls."""

    @staticmethod
    def resolve_connector_name(
        *,
        requested_name: Any,
        allowed_names: list[str] | None,
        family: str,
    ) -> ConnectorAccessDecision:
        requested = requested_name.strip() if isinstance(requested_name, str) else None
        if requested == "":
            requested = None
        if allowed_names is None:
            return ConnectorAccessDecision(connector_name=requested, allowed=True)

        allowed = [name.strip() for name in allowed_names if name.strip()]
        if not allowed:
            return ConnectorAccessDecision(
                connector_name=None,
                allowed=False,
                warning=f"agent_{family}_connectors_disabled",
            )

        if requested is not None:
            if requested in allowed:
                return ConnectorAccessDecision(connector_name=requested, allowed=True)
            return ConnectorAccessDecision(
                connector_name=None,
                allowed=False,
                warning=f"agent_{family}_connector_not_allowed",
            )

        if len(allowed) == 1:
            return ConnectorAccessDecision(connector_name=allowed[0], allowed=True)

        return ConnectorAccessDecision(
            connector_name=None,
            allowed=False,
            warning=f"agent_{family}_connector_required",
        )
