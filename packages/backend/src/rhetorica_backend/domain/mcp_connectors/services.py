from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from rhetorica_backend.domain.chat.error_contract import ErrorCategory, build_tool_error
from rhetorica_backend.domain.mcp_connectors.client import McpRemoteClient
from rhetorica_backend.infra.postgres.mcp_connector_repository import (
    McpConnectorRepository,
)


@dataclass(frozen=True, slots=True)
class McpToolLoadResult:
    """Exact MCP schema load result split from the compact model-facing payload."""

    payload: dict[str, Any]
    definitions: list[dict[str, Any]]


class McpToolCatalogSyncService:
    """Synchronize deterministic MCP tool catalogs into local storage."""

    def __init__(
        self,
        *,
        repository: McpConnectorRepository,
        client: McpRemoteClient | None = None,
    ) -> None:
        self._repository = repository
        self._client = client or McpRemoteClient()

    def sync_connector(self, *, connector_id: str, workspace_id: str) -> tuple[int, str | None]:
        connector = self._repository.get_runtime_connector(
            connector_id=connector_id,
            workspace_id=workspace_id,
        )
        if connector is None:
            return 0, "mcp_connector_disabled"
        try:
            tools = self._client.list_tools(connector)
        except Exception as exc:
            message = McpToolExecutionService._compact_exception_message(exc)
            self._repository.mark_sync_failed(
                connector_id=connector_id,
                workspace_id=workspace_id,
                error_message=message,
            )
            return 0, message

        stored_connector = self._repository.get_by_id(
            connector_id=connector_id,
            workspace_id=workspace_id,
        )
        if not tools and stored_connector is not None and stored_connector.tool_count > 0:
            message = (
                "MCP tools/list returned an empty catalog; preserved "
                f"{stored_connector.tool_count} previously active tools."
            )
            self._repository.mark_sync_failed(
                connector_id=connector_id,
                workspace_id=workspace_id,
                error_message=message,
            )
            return 0, message

        count = self._repository.replace_tool_catalog(
            connector_id=connector_id,
            workspace_id=workspace_id,
            tools=tools,
            status="success",
            error_message=None,
        )
        return count, None


class McpToolCatalogService:
    """Deterministic model-controlled discovery for synced MCP tool catalogs."""

    DEFAULT_PAGE_SIZE = 50
    MAX_PAGE_SIZE = 50

    def __init__(self, *, repository: McpConnectorRepository) -> None:
        self._repository = repository

    def list_tools(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        allowed_connector_names: list[str] | None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        access_error = self._connector_access_error(
            workspace_id=workspace_id,
            connector_name=connector_name,
            allowed_connector_names=allowed_connector_names,
        )
        if access_error is not None:
            return access_error
        if offset < 0 or limit < 1 or limit > self.MAX_PAGE_SIZE:
            return build_tool_error(
                error_code="mcp_catalog_pagination_invalid",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    "MCP catalog offset must be non-negative and limit must be between "
                    f"1 and {self.MAX_PAGE_SIZE}."
                ),
                retry_recommended=False,
                can_continue=True,
            )

        tools, total = self._repository.list_active_tools_for_connector(
            workspace_id=workspace_id,
            connector_name=connector_name,
            offset=offset,
            limit=limit,
        )
        next_offset = offset + len(tools)
        has_more = next_offset < total
        return {
            "ok": True,
            "connector_name": connector_name,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "tools": [
                {
                    "remote_tool_name": item.remote_tool_name,
                    "exposed_tool_name": item.exposed_tool_name,
                    "description": item.description or "",
                }
                for item in tools
            ],
        }

    def load_tools(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        remote_tool_names: list[str],
        allowed_connector_names: list[str] | None,
    ) -> McpToolLoadResult:
        access_error = self._connector_access_error(
            workspace_id=workspace_id,
            connector_name=connector_name,
            allowed_connector_names=allowed_connector_names,
        )
        if access_error is not None:
            return McpToolLoadResult(payload=access_error, definitions=[])
        if (
            not remote_tool_names
            or any(not isinstance(name, str) or not name for name in remote_tool_names)
            or len(set(remote_tool_names)) != len(remote_tool_names)
        ):
            return McpToolLoadResult(
                payload=build_tool_error(
                    error_code="mcp_tool_names_invalid",
                    error_category=ErrorCategory.RECOVERABLE,
                    user_message=("remote_tool_names must contain unique, non-empty exact names."),
                    retry_recommended=False,
                    can_continue=True,
                ),
                definitions=[],
            )

        tools, _total = self._repository.list_active_tools_for_connector(
            workspace_id=workspace_id,
            connector_name=connector_name,
        )
        by_remote_name = {item.remote_tool_name: item for item in tools}
        missing = [name for name in remote_tool_names if name not in by_remote_name]
        if missing:
            error = build_tool_error(
                error_code="mcp_tools_not_found",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=(
                    "One or more exact MCP remote tool names are unavailable. "
                    "Call list_mcp_tools for this connector and retry without substitution."
                ),
                retry_recommended=False,
                can_continue=True,
            )
            error["connector_name"] = connector_name
            error["missing_remote_tool_names"] = missing
            return McpToolLoadResult(payload=error, definitions=[])

        selected = [by_remote_name[name] for name in remote_tool_names]
        definitions = [McpToolExecutionService.tool_definition(item) for item in selected]
        return McpToolLoadResult(
            payload={
                "ok": True,
                "connector_name": connector_name,
                "loaded_tools": [
                    {
                        "remote_tool_name": item.remote_tool_name,
                        "exposed_tool_name": item.exposed_tool_name,
                    }
                    for item in selected
                ],
            },
            definitions=definitions,
        )

    def _connector_access_error(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        allowed_connector_names: list[str] | None,
    ) -> dict[str, Any] | None:
        allowed = self._allowed_connector_set(allowed_connector_names)
        if allowed is not None and connector_name not in allowed:
            return build_tool_error(
                error_code="mcp_connector_not_allowed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="MCP connector is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )
        connector = self._repository.get_by_name(
            name=connector_name,
            workspace_id=workspace_id,
        )
        if connector is None or not connector.is_active:
            return build_tool_error(
                error_code="mcp_connector_unavailable",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="Exact active MCP connector was not found.",
                retry_recommended=False,
                can_continue=True,
            )
        return None

    @staticmethod
    def _allowed_connector_set(allowed_connector_names: list[str] | None) -> set[str] | None:
        if allowed_connector_names is None:
            return None
        return set(allowed_connector_names)


class McpToolExecutionService:
    """Execute synced MCP tools by exact exposed tool name."""

    def __init__(
        self,
        *,
        repository: McpConnectorRepository,
        client: McpRemoteClient | None = None,
    ) -> None:
        self._repository = repository
        self._client = client or McpRemoteClient()

    def tool_definitions(
        self,
        *,
        workspace_id: str,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        return self.tool_definitions_for_connectors(
            workspace_id=workspace_id,
            allowed_connector_names=None,
            limit=limit,
        )

    def external_tool_definitions(
        self,
        *,
        workspace_id: str,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        return [
            self.tool_definition(item)
            for item in self._repository.list_external_tools(
                workspace_id=workspace_id,
                limit=limit,
            )
        ]

    def tool_definitions_for_connectors(
        self,
        *,
        workspace_id: str,
        allowed_connector_names: list[str] | None,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        allowed = self._allowed_connector_set(allowed_connector_names)
        for item in self._repository.list_active_tools(workspace_id=workspace_id, limit=limit):
            if allowed is not None and item.connector_name not in allowed:
                continue
            definitions.append(self.tool_definition(item))
        return definitions

    @staticmethod
    def tool_definition(item: Any) -> dict[str, Any]:
        """Build one OpenAI-compatible function definition from a synced catalog item."""
        return {
            "type": "function",
            "function": {
                "name": item.exposed_tool_name,
                "description": item.description
                or f"Call MCP tool {item.remote_tool_name} on connector {item.connector_name}.",
                "parameters": item.input_schema or {"type": "object", "properties": {}},
            },
        }

    def available_tool_names(
        self,
        *,
        workspace_id: str,
        limit: int | None = 500,
    ) -> list[str]:
        return [
            item.exposed_tool_name
            for item in self._repository.list_active_tools(workspace_id=workspace_id, limit=limit)
        ]

    def is_tool_available(self, *, workspace_id: str, exposed_tool_name: str) -> bool:
        """Check one exact tool through active workspace and admin allowlist policy."""
        return (
            self._repository.get_active_tool(
                workspace_id=workspace_id,
                exposed_tool_name=exposed_tool_name,
            )
            is not None
        )

    def is_external_tool_available(self, *, workspace_id: str, exposed_tool_name: str) -> bool:
        """Check exact tool availability through external connector visibility policy."""
        return (
            self._repository.get_external_tool(
                workspace_id=workspace_id,
                exposed_tool_name=exposed_tool_name,
            )
            is not None
        )

    def execute_external(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self._repository.get_external_tool(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
        )
        return self._execute_tool(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
            arguments=arguments,
            allowed_connector_names=None,
            tool=tool,
        )

    def execute(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
        arguments: dict[str, Any],
        allowed_connector_names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._execute_tool(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
            arguments=arguments,
            allowed_connector_names=allowed_connector_names,
            tool=self._repository.get_tool(
                workspace_id=workspace_id,
                exposed_tool_name=exposed_tool_name,
            ),
        )

    def _execute_tool(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
        arguments: dict[str, Any],
        allowed_connector_names: list[str] | None,
        tool: Any,
    ) -> dict[str, Any]:
        if tool is None or not tool.is_active:
            return build_tool_error(
                error_code="mcp_tool_disabled",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="MCP tool is disabled or not present in the synced catalog.",
                retry_recommended=False,
                can_continue=True,
            )

        allowed = self._allowed_connector_set(allowed_connector_names)
        if allowed is not None and tool.connector_name not in allowed:
            return build_tool_error(
                error_code="mcp_tool_disabled",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="MCP tool is not allowed for the selected agent.",
                retry_recommended=False,
                can_continue=True,
            )

        connector = self._repository.get_runtime_connector(
            connector_id=tool.connector_id,
            workspace_id=workspace_id,
        )
        if connector is None:
            return build_tool_error(
                error_code="mcp_connector_disabled",
                error_category=ErrorCategory.RECOVERABLE,
                user_message="MCP connector is disabled.",
                retry_recommended=False,
                can_continue=True,
            )

        validation_error = self._validate_arguments(
            input_schema=tool.input_schema,
            arguments=arguments,
        )
        if validation_error is not None:
            error = build_tool_error(
                error_code="mcp_tool_arguments_invalid",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"MCP tool arguments do not match synced schema: {validation_error}",
                retry_recommended=False,
                can_continue=True,
            )
            error["connector_name"] = connector.name
            error["remote_tool_name"] = tool.remote_tool_name
            error["validation_error"] = validation_error
            return error

        try:
            result = self._client.call_tool(
                connector,
                tool_name=tool.remote_tool_name,
                arguments=arguments,
            )
        except Exception as exc:
            error_message = self._compact_exception_message(exc)
            error = build_tool_error(
                error_code="mcp_tool_call_failed",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"MCP tool call failed: {error_message}",
                retry_recommended=False,
                can_continue=True,
            )
            error["error_message"] = error_message
            error["connector_name"] = connector.name
            error["remote_tool_name"] = tool.remote_tool_name
            return error

        if result.get("isError") is True:
            error_message = self._remote_error_message(result)
            error = build_tool_error(
                error_code="mcp_tool_remote_error",
                error_category=ErrorCategory.RECOVERABLE,
                user_message=f"MCP tool returned an error: {error_message}",
                retry_recommended=False,
                can_continue=True,
            )
            error["error_message"] = error_message
            error["connector_name"] = connector.name
            error["remote_tool_name"] = tool.remote_tool_name
            error["result"] = result
            return error

        return {
            "ok": True,
            "connector_name": connector.name,
            "remote_tool_name": tool.remote_tool_name,
            "result": result,
        }

    @staticmethod
    def _allowed_connector_set(allowed_connector_names: list[str] | None) -> set[str] | None:
        if allowed_connector_names is None:
            return None
        return {name for name in allowed_connector_names if name}

    @staticmethod
    def _validate_arguments(
        *,
        input_schema: dict[str, Any],
        arguments: dict[str, Any],
    ) -> str | None:
        schema = input_schema or {"type": "object", "properties": {}}
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except SchemaError:
            return None
        errors = sorted(
            validator.iter_errors(arguments),
            key=lambda item: list(item.path),
        )
        if not errors:
            return None
        return "; ".join(error.message for error in errors[:5])[:1000]

    @classmethod
    def _compact_exception_message(cls, exc: BaseException) -> str:
        leaves = list(cls._exception_leaves(exc))
        messages = [
            cls._sanitize_exception_text(str(item))
            for item in leaves
            if cls._sanitize_exception_text(str(item))
        ]
        if not messages:
            messages = [type(item).__name__ for item in leaves] or [type(exc).__name__]
        return "; ".join(dict.fromkeys(messages))[:1000]

    @staticmethod
    def _remote_error_message(result: dict[str, Any]) -> str:
        messages = [
            " ".join(text.split())
            for item in result.get("content", [])
            if isinstance(item, dict)
            and isinstance((text := item.get("text")), str)
            and text.strip()
        ]
        return "; ".join(dict.fromkeys(messages))[:1000] or "MCP server reported an error."

    @classmethod
    def _exception_leaves(cls, exc: BaseException):
        if isinstance(exc, BaseExceptionGroup):
            for item in exc.exceptions:
                yield from cls._exception_leaves(item)
            return
        yield exc

    @staticmethod
    def _sanitize_exception_text(value: str) -> str:
        return " ".join(value.split())
