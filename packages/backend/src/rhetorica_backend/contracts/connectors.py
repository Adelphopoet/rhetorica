from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from rhetorica_backend.contracts.execution import EngineConnectionConfig


class UpsertConnectorRequest(BaseModel):
    """Create or update a named connector inside a workspace."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: EngineConnectionConfig


class ConnectorItem(BaseModel):
    """Connector payload returned to API clients."""

    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: EngineConnectionConfig
    has_password: bool
    created_at: datetime
    updated_at: datetime
