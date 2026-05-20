"""Metadata connection resolution primitives."""

from __future__ import annotations

from copy import copy
from typing import Any

from rhetorica_backend.contracts.admin_integrations import OpenMetadataConnectionConfig


class MetadataResolver:
    """Resolve metadata connector connections with explicit fallback rules."""

    def __init__(self, openmetadata_connector_repository: Any):
        self._openmetadata_connector_repository = openmetadata_connector_repository

    def resolve_connection(
        self,
        workspace_id: str,
        requested_name: str | None,
        fallback_name: str | None,
        warnings: list[str],
    ) -> OpenMetadataConnectionConfig | None:
        _ = warnings
        connection = self._openmetadata_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=requested_name,
        )
        if connection is not None:
            return connection

        if requested_name:
            if fallback_name and fallback_name != requested_name:
                fallback_connection = self._openmetadata_connector_repository.get_connection(
                    workspace_id=workspace_id,
                    name=fallback_name,
                )
                if fallback_connection is not None:
                    return fallback_connection
            return None

        return self._openmetadata_connector_repository.get_connection(
            workspace_id=workspace_id
        )


def clone_metadata_connection_with_scope(
    connection: OpenMetadataConnectionConfig | Any,
    *,
    service_name: str | None,
    database_name: str | None,
    schema_name: str | None,
) -> OpenMetadataConnectionConfig | Any:
    """Clone metadata connection while overriding scope fields."""
    if hasattr(connection, "model_copy"):
        return connection.model_copy(
            update={
                "service_name": service_name,
                "database_name": database_name,
                "schema_name": schema_name,
            }
        )
    cloned = copy(connection)
    cloned.service_name = service_name
    cloned.database_name = database_name
    cloned.schema_name = schema_name
    return cloned
