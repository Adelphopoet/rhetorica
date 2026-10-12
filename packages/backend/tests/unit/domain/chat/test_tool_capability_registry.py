from __future__ import annotations

from rhetorica_backend.contracts.chat import ChatAskRequest
from rhetorica_backend.domain.chat.orchestration.state import ToolOrchestrationState
from rhetorica_backend.domain.chat.orchestration.tool_capability import (
    BoundToolCapability,
)
from rhetorica_backend.domain.chat.orchestration.tool_capability_registry import (
    ToolCapabilityRegistry,
)


def test_tool_capability_registry_resolves_and_executes_bound_capability() -> None:
    called = {}

    def _handler(**kwargs):
        called["payload"] = kwargs
        return {"ok": True}

    registry = ToolCapabilityRegistry(
        [
            BoundToolCapability(
                name="catalog__search",
                requires_db_connectors=True,
                handler=_handler,
            )
        ]
    )

    capability = registry.get("catalog__search")

    assert capability is not None
    result = capability.execute(
        request=ChatAskRequest(question="find table", workspace_id="default"),
        arguments={"query": "orders"},
        state=ToolOrchestrationState(),
        db_connectors=[],
    )

    assert result["ok"] is True
    assert called["payload"]["arguments"] == {"query": "orders"}
    assert "db_connectors" in called["payload"]


def test_tool_capability_registry_returns_none_for_unknown_name() -> None:
    registry = ToolCapabilityRegistry([])

    assert registry.get("missing") is None
