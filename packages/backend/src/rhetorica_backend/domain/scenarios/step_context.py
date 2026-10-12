from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rhetorica_backend.infra.postgres.user_repository import UserAccountItem


@dataclass(frozen=True)
class ScenarioStepContext:
    run_id: str
    node_id: str
    operation_id: str
    attempt: int
    user: UserAccountItem
    conversation_id: str
    agent_snapshot: dict[str, Any] | None = None
