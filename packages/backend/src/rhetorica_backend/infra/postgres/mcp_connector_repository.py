from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2s
from secrets import choice
from typing import Any
from uuid import uuid4

from rhetorica_backend.contracts.admin_integrations import (
    McpConnectionConfig,
    McpConnectorItem,
    McpSecretHeaderMode,
    McpToolCatalogItem,
    UpsertMcpConnectorRequest,
)
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.infra.postgres.llm_connector_repository import LlmConnectorRepository
from rhetorica_backend.infra.postgres.models import McpConnectorConfigOrm, McpToolCatalogOrm
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.workspace_resource_access import (
    AccessibleResource,
    WorkspaceResourceAccess,
    present_resource,
)
from rhetorica_backend.infra.secrets import SecretStore, SecretStoreError, get_secret_store
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

MAX_EXPOSED_TOOL_NAME_LENGTH = 64


@dataclass(frozen=True)
class ResolvedMcpConnector:
    """Runtime MCP connector with sensitive headers resolved from SecretStore."""

    id: str
    workspace_id: str
    name: str
    provider: str
    transport: str
    url: str
    verify_tls: bool
    headers: dict[str, str]
    timeout_seconds: float
    tool_name_prefix: str


class McpConnectorRepository:
    """Persistence gateway for remote MCP connector settings and tool catalog."""

    def __init__(
        self,
        db: DatabaseRuntime,
        secret_store: SecretStore | None = None,
        llm_connector_repository: LlmConnectorRepository | None = None,
    ):
        self._db = db
        self._secret_store = secret_store or get_secret_store()
        self._llm_connector_repository = llm_connector_repository or LlmConnectorRepository(
            db=db,
            secret_store=self._secret_store,
        )
        self._resources = WorkspaceResourceAccess(
            db,
            kind=WorkspaceResourceKind.MCP_CONNECTOR,
            model=McpConnectorConfigOrm,
        )

    def upsert(self, request: UpsertMcpConnectorRequest) -> McpConnectorItem:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.execute(
                select(McpConnectorConfigOrm).where(
                    McpConnectorConfigOrm.workspace_id == request.workspace_id,
                    McpConnectorConfigOrm.name == request.name,
                )
            ).scalar_one_or_none()
            if row is None:
                row = McpConnectorConfigOrm(
                    id=str(uuid4()),
                    workspace_id=request.workspace_id,
                    name=request.name,
                    is_active=request.is_active,
                    visible_to_external_mcp=request.visible_to_external_mcp,
                    provider=request.connection.provider,
                    transport=request.connection.transport,
                    url=request.connection.url,
                    verify_tls=request.connection.verify_tls,
                    headers=dict(request.connection.headers),
                    secret_header_refs={},
                    enabled_groups=request.connection.enabled_groups,
                    allowed_tool_names=list(request.connection.allowed_tool_names),
                    timeout_seconds=request.connection.timeout_seconds,
                    tool_name_prefix=self._tool_name_prefix(
                        request.name,
                        request.connection.tool_name_prefix,
                    ),
                    llm_connector_name=request.connection.llm_connector_name,
                    usage_description=request.connection.usage_description,
                    last_sync_status="pending",
                    extra_settings=request.connection.extra_settings,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.updated_at = now
                row.is_active = request.is_active
                row.visible_to_external_mcp = request.visible_to_external_mcp
                row.provider = request.connection.provider
                row.transport = request.connection.transport
                row.url = request.connection.url
                row.verify_tls = request.connection.verify_tls
                row.headers = dict(request.connection.headers)
                row.enabled_groups = request.connection.enabled_groups
                row.allowed_tool_names = list(request.connection.allowed_tool_names)
                row.timeout_seconds = request.connection.timeout_seconds
                row.tool_name_prefix = self._tool_name_prefix(
                    request.name,
                    request.connection.tool_name_prefix,
                )
                row.llm_connector_name = request.connection.llm_connector_name
                row.usage_description = request.connection.usage_description
                row.extra_settings = request.connection.extra_settings

            if request.connection.secret_headers is not None:
                row.secret_header_refs = self._store_secret_headers(
                    workspace_id=request.workspace_id,
                    connector_id=row.id,
                    secret_headers=request.connection.secret_headers,
                    previous_refs=row.secret_header_refs or {},
                )

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row, tool_count=self._tool_count(row.id))

    def update(
        self,
        connector_id: str,
        workspace_id: str,
        request: UpsertMcpConnectorRequest,
    ) -> McpConnectorItem | None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(McpConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.updated_at = now
            row.name = request.name
            row.is_active = request.is_active
            row.visible_to_external_mcp = request.visible_to_external_mcp
            row.provider = request.connection.provider
            row.transport = request.connection.transport
            row.url = request.connection.url
            row.verify_tls = request.connection.verify_tls
            row.headers = dict(request.connection.headers)
            row.enabled_groups = request.connection.enabled_groups
            row.allowed_tool_names = list(request.connection.allowed_tool_names)
            row.timeout_seconds = request.connection.timeout_seconds
            row.tool_name_prefix = self._tool_name_prefix(
                request.name,
                request.connection.tool_name_prefix,
            )
            row.llm_connector_name = request.connection.llm_connector_name
            row.usage_description = request.connection.usage_description
            row.extra_settings = request.connection.extra_settings
            if request.connection.secret_headers is not None:
                row.secret_header_refs = self._store_secret_headers(
                    workspace_id=workspace_id,
                    connector_id=row.id,
                    secret_headers=request.connection.secret_headers,
                    previous_refs=row.secret_header_refs or {},
                )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise
            session.refresh(row)
            return self._to_contract(row, tool_count=self._tool_count(row.id))

    def list(self, workspace_id: str, limit: int | None) -> list[McpConnectorItem]:
        resources = self._resources.list(workspace_id=workspace_id, limit=limit)
        with self._db.session() as session:
            counts = self._tool_counts_by_connector(
                session,
                [resource.row.id for resource in resources],
            )
        return [
            present_resource(
                self._to_contract(resource.row, tool_count=counts.get(resource.row.id, 0)),
                resource,
            )
            for resource in resources
        ]

    def get_by_id(self, connector_id: str, workspace_id: str) -> McpConnectorItem | None:
        resource = self._resources.by_id(
            workspace_id=workspace_id,
            resource_id=connector_id,
        )
        if resource is None:
            return None
        return present_resource(
            self._to_contract(resource.row, tool_count=self._tool_count(resource.row.id)),
            resource,
        )

    def get_by_name(self, *, name: str, workspace_id: str) -> McpConnectorItem | None:
        """Resolve one connector by its exact workspace-local name."""
        resource = next(
            (
                item
                for item in self._resources.list(workspace_id=workspace_id, limit=None)
                if item.alias == name
            ),
            None,
        )
        if resource is None:
            return None
        return present_resource(
            self._to_contract(resource.row, tool_count=self._tool_count(resource.row.id)),
            resource,
        )

    def get_runtime_connector(
        self,
        *,
        connector_id: str | None = None,
        workspace_id: str,
        name: str | None = None,
    ) -> ResolvedMcpConnector | None:
        if connector_id:
            resource = self._resources.by_id(
                workspace_id=workspace_id,
                resource_id=connector_id,
            )
        else:
            resource = self._resources.first_active(workspace_id=workspace_id, name=name)
        if resource is None or not resource.row.is_active:
            return None
        return self._to_runtime(resource.row)

    def delete(self, connector_id: str, workspace_id: str) -> bool:
        with self._db.session() as session:
            row = session.get(McpConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return False
            for item in (row.secret_header_refs or {}).values():
                if isinstance(item, dict):
                    self._delete_owned_secret(
                        workspace_id=row.workspace_id,
                        connector_id=row.id,
                        ref_payload=item,
                    )
            session.delete(row)
            session.commit()
            return True

    def replace_tool_catalog(
        self,
        *,
        connector_id: str,
        workspace_id: str,
        tools: list[dict[str, Any]],
        status: str,
        error_message: str | None = None,
    ) -> int:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            connector = session.get(McpConnectorConfigOrm, connector_id)
            if connector is None or connector.workspace_id != workspace_id:
                return 0

            existing_rows = {
                row.remote_tool_name: row
                for row in session.execute(
                    select(McpToolCatalogOrm).where(
                        McpToolCatalogOrm.connector_id == connector_id,
                    )
                ).scalars()
            }
            reserved_exposed_names = {
                str(name)
                for name in session.execute(
                    select(McpToolCatalogOrm.exposed_tool_name).where(
                        McpToolCatalogOrm.workspace_id == workspace_id,
                        McpToolCatalogOrm.connector_id != connector_id,
                    )
                ).scalars()
            }
            remote_names = [
                str(tool.get("name") or "").strip()
                for tool in tools
                if str(tool.get("name") or "").strip()
            ]
            base_counts: dict[str, int] = {}
            for remote_name in remote_names:
                base = self.exposed_tool_name(
                    connector_name=connector.name,
                    remote_tool_name=remote_name,
                    tool_name_prefix=connector.tool_name_prefix,
                )
                base_counts[base] = base_counts.get(base, 0) + 1
            seen: set[str] = set()
            seen_exposed_names: set[str] = set()
            for tool in tools:
                remote_name = str(tool.get("name") or "").strip()
                if not remote_name:
                    continue
                seen.add(remote_name)
                row = existing_rows.get(remote_name)
                exposed_name = self.exposed_tool_name(
                    connector_name=connector.name,
                    remote_tool_name=remote_name,
                    tool_name_prefix=connector.tool_name_prefix,
                )
                exposed_name = self._dedupe_exposed_tool_name(
                    base_name=exposed_name,
                    remote_tool_name=remote_name,
                    connector_id=connector.id,
                    base_collision=base_counts.get(exposed_name, 0) > 1,
                    reserved_names=reserved_exposed_names,
                    seen_names=seen_exposed_names,
                )
                seen_exposed_names.add(exposed_name)
                if row is None:
                    row = McpToolCatalogOrm(
                        id=str(uuid4()),
                        connector_id=connector.id,
                        workspace_id=connector.workspace_id,
                        connector_name=connector.name,
                        remote_tool_name=remote_name,
                        exposed_tool_name=exposed_name,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                row.connector_name = connector.name
                row.exposed_tool_name = exposed_name
                row.description = self._optional_str(tool.get("description"))
                row.input_schema = self._schema_dict(tool.get("input_schema"))
                row.is_active = True
                row.updated_at = now

            for remote_name, row in existing_rows.items():
                if remote_name not in seen:
                    row.is_active = False
                    row.updated_at = now

            connector.last_sync_status = status
            connector.last_sync_error = error_message
            connector.last_sync_at = now
            connector.updated_at = now
            session.commit()
            return len(seen)

    def mark_sync_failed(self, *, connector_id: str, workspace_id: str, error_message: str) -> None:
        now = datetime.now(tz=UTC)
        with self._db.session() as session:
            row = session.get(McpConnectorConfigOrm, connector_id)
            if row is None or row.workspace_id != workspace_id:
                return
            row.last_sync_status = "failed"
            row.last_sync_error = error_message[:2000]
            row.last_sync_at = now
            row.updated_at = now
            session.commit()

    def list_active_tools(
        self,
        workspace_id: str,
        limit: int | None = 500,
    ) -> list[McpToolCatalogItem]:
        return self._list_active_tools(
            workspace_id=workspace_id,
            limit=limit,
            external_mcp_only=False,
        )

    def list_external_tools(
        self,
        workspace_id: str,
        limit: int | None = 500,
    ) -> list[McpToolCatalogItem]:
        """List tools explicitly published through the external MCP proxy."""
        return self._list_active_tools(
            workspace_id=workspace_id,
            limit=limit,
            external_mcp_only=True,
        )

    def _list_active_tools(
        self,
        *,
        workspace_id: str,
        limit: int | None,
        external_mcp_only: bool,
    ) -> list[McpToolCatalogItem]:
        tools: list[McpToolCatalogItem] = []
        resources = self._resources.list(workspace_id=workspace_id, limit=None)
        with self._db.session() as session:
            for resource in resources:
                connector = resource.row
                if not connector.is_active or (
                    external_mcp_only and not connector.visible_to_external_mcp
                ):
                    continue
                rows = (
                    session.execute(
                        select(McpToolCatalogOrm)
                        .where(
                            McpToolCatalogOrm.connector_id == connector.id,
                            McpToolCatalogOrm.workspace_id == connector.workspace_id,
                            McpToolCatalogOrm.is_active.is_(True),
                        )
                        .order_by(McpToolCatalogOrm.remote_tool_name.asc())
                    )
                    .scalars()
                    .all()
                )
                tools.extend(
                    self._present_tool(tool, resource)
                    for tool in rows
                    if self._is_tool_allowed(
                        remote_tool_name=tool.remote_tool_name,
                        allowed_tool_names=connector.allowed_tool_names,
                    )
                )
        tools.sort(key=lambda item: (item.connector_name, item.remote_tool_name))
        return tools if limit is None else tools[:limit]

    def list_active_tools_for_connector(
        self,
        *,
        workspace_id: str,
        connector_name: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[McpToolCatalogItem], int]:
        """List an exact active connector catalog after applying its tool allowlist."""
        resource = self._resources.by_name(workspace_id=workspace_id, name=connector_name)
        if resource is None or not resource.row.is_active:
            return [], 0
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(McpToolCatalogOrm)
                    .where(
                        McpToolCatalogOrm.connector_id == resource.row.id,
                        McpToolCatalogOrm.workspace_id == resource.row.workspace_id,
                        McpToolCatalogOrm.is_active.is_(True),
                    )
                    .order_by(McpToolCatalogOrm.remote_tool_name.asc())
                )
                .scalars()
                .all()
            )
        allowed_tools = [
            self._present_tool(tool, resource)
            for tool in rows
            if self._is_tool_allowed(
                remote_tool_name=tool.remote_tool_name,
                allowed_tool_names=resource.row.allowed_tool_names,
            )
        ]
        total = len(allowed_tools)
        page = allowed_tools[offset:] if limit is None else allowed_tools[offset : offset + limit]
        return page, total

    def list_connector_tools(
        self,
        *,
        connector_id: str,
        workspace_id: str,
        limit: int = 500,
    ) -> list[McpToolCatalogItem]:
        resource = self._resources.by_id(
            workspace_id=workspace_id,
            resource_id=connector_id,
        )
        if resource is None:
            return []
        with self._db.session() as session:
            rows = (
                session.execute(
                    select(McpToolCatalogOrm)
                    .where(
                        McpToolCatalogOrm.workspace_id == resource.row.workspace_id,
                        McpToolCatalogOrm.connector_id == resource.row.id,
                        McpToolCatalogOrm.is_active.is_(True),
                    )
                    .order_by(McpToolCatalogOrm.remote_tool_name.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [self._present_tool(row, resource) for row in rows]

    def get_active_tool(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
    ) -> McpToolCatalogItem | None:
        return self._get_active_tool(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
            external_mcp_only=False,
        )

    def get_external_tool(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
    ) -> McpToolCatalogItem | None:
        """Resolve one active tool only when its connector is published externally."""
        return self._get_active_tool(
            workspace_id=workspace_id,
            exposed_tool_name=exposed_tool_name,
            external_mcp_only=True,
        )

    def _get_active_tool(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
        external_mcp_only: bool,
    ) -> McpToolCatalogItem | None:
        return next(
            (
                tool
                for tool in self._list_active_tools(
                    workspace_id=workspace_id,
                    limit=None,
                    external_mcp_only=external_mcp_only,
                )
                if tool.exposed_tool_name == exposed_tool_name
            ),
            None,
        )

    def get_tool(
        self,
        *,
        workspace_id: str,
        exposed_tool_name: str,
    ) -> McpToolCatalogItem | None:
        for resource in self._resources.list(workspace_id=workspace_id, limit=None):
            with self._db.session() as session:
                rows = (
                    session.execute(
                        select(McpToolCatalogOrm).where(
                            McpToolCatalogOrm.connector_id == resource.row.id,
                            McpToolCatalogOrm.workspace_id == resource.row.workspace_id,
                        )
                    )
                    .scalars()
                    .all()
                )
            for row in rows:
                tool = self._present_tool(row, resource)
                if (
                    tool.exposed_tool_name == exposed_tool_name
                    and self._is_tool_allowed(
                        remote_tool_name=row.remote_tool_name,
                        allowed_tool_names=resource.row.allowed_tool_names,
                    )
                ):
                    return tool
        return None

    def _present_tool(
        self,
        row: McpToolCatalogOrm,
        resource: AccessibleResource[McpConnectorConfigOrm],
    ) -> McpToolCatalogItem:
        item = self._tool_to_contract(row)
        if not resource.is_shared:
            return item
        return item.model_copy(
            update={
                "workspace_id": resource.requested_workspace_id,
                "connector_name": resource.alias,
                "exposed_tool_name": self.exposed_tool_name(
                    connector_name=resource.alias,
                    remote_tool_name=row.remote_tool_name,
                    tool_name_prefix=None,
                ),
            }
        )

    @staticmethod
    def exposed_tool_name(
        *,
        connector_name: str,
        remote_tool_name: str,
        tool_name_prefix: str | None,
    ) -> str:
        prefix = tool_name_prefix or McpConnectorRepository._tool_name_prefix(connector_name, None)
        remote_fragment = McpConnectorRepository._tool_name_fragment(remote_tool_name, "tool")
        return McpConnectorRepository._bounded_tool_name(f"{prefix}__{remote_fragment}")

    def _to_runtime(self, row: McpConnectorConfigOrm) -> ResolvedMcpConnector:
        return ResolvedMcpConnector(
            id=row.id,
            workspace_id=row.workspace_id,
            name=row.name,
            provider=row.provider,
            transport=row.transport,
            url=row.url,
            verify_tls=row.verify_tls,
            headers=self._resolve_headers(row),
            timeout_seconds=row.timeout_seconds,
            tool_name_prefix=row.tool_name_prefix or self._tool_name_prefix(row.name, None),
        )

    def _resolve_headers(self, row: McpConnectorConfigOrm) -> dict[str, str]:
        headers = dict(row.headers or {})
        for header_name, ref_payload in (row.secret_header_refs or {}).items():
            if not isinstance(ref_payload, dict):
                continue
            secret_ref = ref_payload.get("secret_ref")
            if not isinstance(secret_ref, str) or not secret_ref:
                continue
            secret = self._secret_store.get_secret(
                workspace_id=row.workspace_id,
                secret_key=secret_ref,
            )
            if not secret:
                continue
            mode = self._normalize_mode(ref_payload.get("mode"))
            headers[header_name] = f"Bearer {secret}" if mode == "bearer" else secret
        if row.enabled_groups and "Enabled-Groups" not in headers:
            headers["Enabled-Groups"] = row.enabled_groups
        if row.llm_connector_name and "apiKey" not in headers:
            api_key = self._select_llm_api_key(
                workspace_id=row.workspace_id,
                llm_connector_name=row.llm_connector_name,
            )
            if api_key:
                headers["apiKey"] = api_key
        return headers

    def _select_llm_api_key(self, *, workspace_id: str, llm_connector_name: str) -> str | None:
        connection = self._llm_connector_repository.get_connection(
            workspace_id=workspace_id,
            name=llm_connector_name,
        )
        if connection is None:
            return None
        if connection.api_keys:
            return choice(connection.api_keys)
        return connection.api_key

    def _to_contract(self, row: McpConnectorConfigOrm, *, tool_count: int) -> McpConnectorItem:
        secret_names = sorted((row.secret_header_refs or {}).keys())
        return McpConnectorItem(
            id=row.id,
            name=row.name,
            workspace_id=row.workspace_id,
            is_active=row.is_active,
            visible_to_external_mcp=row.visible_to_external_mcp,
            connection=McpConnectionConfig(
                provider=row.provider,
                transport=row.transport,
                url=row.url,
                verify_tls=row.verify_tls,
                headers=dict(row.headers or {}),
                secret_headers=None,
                enabled_groups=row.enabled_groups,
                allowed_tool_names=list(row.allowed_tool_names or []),
                timeout_seconds=row.timeout_seconds,
                tool_name_prefix=row.tool_name_prefix,
                llm_connector_name=row.llm_connector_name,
                usage_description=row.usage_description,
                extra_settings=row.extra_settings or {},
            ),
            has_secret_headers=bool(secret_names),
            secret_header_names=secret_names,
            tool_count=tool_count,
            last_sync_status=row.last_sync_status,
            last_sync_error=row.last_sync_error,
            last_sync_at=row.last_sync_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _tool_to_contract(row: McpToolCatalogOrm) -> McpToolCatalogItem:
        return McpToolCatalogItem(
            id=row.id,
            connector_id=row.connector_id,
            workspace_id=row.workspace_id,
            connector_name=row.connector_name,
            remote_tool_name=row.remote_tool_name,
            exposed_tool_name=row.exposed_tool_name,
            description=row.description,
            input_schema=row.input_schema or {},
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _is_tool_allowed(
        *,
        remote_tool_name: str,
        allowed_tool_names: list[str] | None,
    ) -> bool:
        if not allowed_tool_names:
            return True
        return remote_tool_name in allowed_tool_names

    def _store_secret_headers(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        secret_headers: list[Any],
        previous_refs: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        next_refs: dict[str, dict[str, str]] = {}
        for header in secret_headers:
            header_name = header.name.strip()
            secret_ref = self._secret_header_ref(
                workspace_id=workspace_id,
                connector_id=connector_id,
                header_name=header_name,
            )
            self._secret_store.set_secret(
                workspace_id=workspace_id,
                secret_key=secret_ref,
                value=header.value,
            )
            next_refs[header_name] = {"secret_ref": secret_ref, "mode": header.mode}

        for header_name, payload in previous_refs.items():
            if header_name in next_refs or not isinstance(payload, dict):
                continue
            self._delete_owned_secret(
                workspace_id=workspace_id,
                connector_id=connector_id,
                ref_payload=payload,
            )
        return next_refs

    def _tool_count(self, connector_id: str) -> int:
        with self._db.session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(McpToolCatalogOrm)
                    .where(
                        McpToolCatalogOrm.connector_id == connector_id,
                        McpToolCatalogOrm.is_active.is_(True),
                    )
                ).scalar_one()
                or 0
            )

    @staticmethod
    def _tool_counts_by_connector(session: Any, connector_ids: list[str]) -> dict[str, int]:
        if not connector_ids:
            return {}
        rows = session.execute(
            select(McpToolCatalogOrm.connector_id, func.count())
            .where(
                McpToolCatalogOrm.connector_id.in_(connector_ids),
                McpToolCatalogOrm.is_active.is_(True),
            )
            .group_by(McpToolCatalogOrm.connector_id)
        ).all()
        return {str(connector_id): int(count) for connector_id, count in rows}

    @staticmethod
    def _tool_name_prefix(name: str, configured: str | None) -> str:
        raw = (configured or name).strip()
        normalized = "".join(ch if ch.isalnum() else "_" for ch in raw.lower())
        normalized = "_".join(part for part in normalized.split("_") if part)
        return normalized or "mcp"

    @staticmethod
    def _tool_name_fragment(value: str, fallback: str) -> str:
        normalized = "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())
        normalized = "_".join(part for part in normalized.split("_") if part)
        return normalized or fallback

    @staticmethod
    def _bounded_tool_name(value: str, *, hash_source: str | None = None) -> str:
        if len(value) <= MAX_EXPOSED_TOOL_NAME_LENGTH:
            return value
        digest = blake2s((hash_source or value).encode("utf-8"), digest_size=4).hexdigest()
        suffix = f"_{digest}"
        head = value[: MAX_EXPOSED_TOOL_NAME_LENGTH - len(suffix)].rstrip("_")
        return f"{head}{suffix}" or f"mcp_{digest}"

    @classmethod
    def _dedupe_exposed_tool_name(
        cls,
        *,
        base_name: str,
        remote_tool_name: str,
        connector_id: str,
        base_collision: bool,
        reserved_names: set[str],
        seen_names: set[str],
    ) -> str:
        candidate = base_name
        if base_collision or candidate in reserved_names or candidate in seen_names:
            candidate = cls._bounded_tool_name(
                f"{base_name}_{cls._hash_suffix(remote_tool_name)}",
                hash_source=f"{connector_id}:{remote_tool_name}",
            )
        counter = 2
        while candidate in reserved_names or candidate in seen_names:
            candidate = cls._bounded_tool_name(
                f"{base_name}_{cls._hash_suffix(f'{connector_id}:{remote_tool_name}:{counter}')}",
                hash_source=f"{connector_id}:{remote_tool_name}:{counter}",
            )
            counter += 1
        return candidate

    @staticmethod
    def _hash_suffix(value: str) -> str:
        return blake2s(value.encode("utf-8"), digest_size=4).hexdigest()

    @staticmethod
    def _secret_header_ref(workspace_id: str, connector_id: str, header_name: str) -> str:
        normalized = "".join(ch if ch.isalnum() else "_" for ch in header_name.lower()).strip("_")
        return f"mcp_connector/{workspace_id}/{connector_id}/header/{normalized}"

    @classmethod
    def _is_owned_secret_ref(cls, *, workspace_id: str, connector_id: str, secret_ref: str) -> bool:
        return secret_ref.startswith(f"mcp_connector/{workspace_id}/{connector_id}/")

    @staticmethod
    def _normalize_mode(value: Any) -> McpSecretHeaderMode:
        return "bearer" if value == "bearer" else "raw"

    @staticmethod
    def _schema_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    def _delete_secret(self, workspace_id: str, secret_ref: Any) -> None:
        if not isinstance(secret_ref, str) or not secret_ref:
            return
        try:
            self._secret_store.delete_secret(workspace_id=workspace_id, secret_key=secret_ref)
        except SecretStoreError:
            return

    def _delete_owned_secret(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        ref_payload: dict[str, Any],
    ) -> None:
        secret_ref = ref_payload.get("secret_ref")
        if not isinstance(secret_ref, str) or not secret_ref:
            return
        if not self._is_owned_secret_ref(
            workspace_id=workspace_id,
            connector_id=connector_id,
            secret_ref=secret_ref,
        ):
            return
        self._delete_secret(workspace_id, secret_ref)
