"""Task-scoped Trino protocol gateway and dbt profile issuer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from rhetorica_backend.contracts.coding import (
    DbtRuntimeProfile,
    IssueDbtProfileRequest,
    IssuedDbtProfile,
    IssuedQueryCapability,
    IssueQueryCapabilityRequest,
    QueryCapabilityClaims,
)
from rhetorica_backend.contracts.execution import EngineConnectionConfig
from rhetorica_backend.contracts.workspaces import WorkspaceResourceKind
from rhetorica_backend.domain.coding.trino_sql_policy import StrictTrinoReadOnlyPolicy
from rhetorica_backend.infra.postgres.agent_repository import AgentProfileRepository
from rhetorica_backend.infra.postgres.connector_repository import ConnectorRepository
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository
from rhetorica_backend.infra.secrets import SecretStoreError


class QueryGatewayDenied(ValueError):
    def __init__(self, reason: str, *, status_code: int = 403):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


class QueryGatewayProtocolError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int = 502):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


class QueryCapabilitySigner:
    def __init__(self, secret: str):
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("coding_query_gateway_secret_too_short")
        self._secret = secret.encode("utf-8")

    def issue(self, claims: QueryCapabilityClaims) -> str:
        payload = claims.model_dump_json().encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def verify(self, token: str) -> QueryCapabilityClaims:
        try:
            payload_part, signature_part = token.split(".", 1)
            encoded = payload_part.encode("ascii")
            signature = self._decode(signature_part)
            expected = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        except (ValueError, UnicodeError) as exc:
            raise QueryGatewayDenied("query_capability_invalid", status_code=401) from exc
        if not hmac.compare_digest(signature, expected):
            raise QueryGatewayDenied("query_capability_invalid", status_code=401)
        try:
            claims = QueryCapabilityClaims.model_validate_json(self._decode(payload_part))
        except (ValueError, UnicodeError) as exc:
            raise QueryGatewayDenied("query_capability_invalid", status_code=401) from exc
        if claims.expires_at <= datetime.now(tz=UTC):
            raise QueryGatewayDenied("query_capability_expired", status_code=401)
        return claims

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


def connector_endpoint_digest(connection: EngineConnectionConfig) -> str:
    identity = {
        "engine": connection.engine,
        "host": connection.host.strip().lower(),
        "port": connection.port,
        "secure": connection.secure,
        "verify": connection.verify,
    }
    return hashlib.sha256(
        json.dumps(identity, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


class LiveDatabaseCapabilityResolver:
    """Resolve credentials only after live workspace, resource and agent checks."""

    def __init__(
        self,
        *,
        connectors: ConnectorRepository,
        workspaces: WorkspaceRepository,
        agents: AgentProfileRepository,
    ) -> None:
        self._connectors = connectors
        self._workspaces = workspaces
        self._agents = agents

    def resolve_for_issue(
        self, request: IssueDbtProfileRequest | IssueQueryCapabilityRequest
    ) -> tuple[str, EngineConnectionConfig]:
        principal = request.principal
        if principal.task_id is None or principal.executing_agent_id is None:
            raise QueryGatewayDenied("query_capability_scope_invalid")
        membership = self._workspaces.membership(
            workspace_id=principal.workspace_id, user_id=principal.actor_user_id
        )
        if membership is None or str(membership.role) not in {"owner", "analyst"}:
            raise QueryGatewayDenied("workspace_access_denied")
        resolved = self._resolve_connection(
            workspace_id=principal.workspace_id, connector_id=request.connector_id
        )
        if resolved is None:
            raise QueryGatewayDenied("resource_access_revoked")
        alias, connection = resolved
        self._require_agent_access(
            workspace_id=principal.workspace_id,
            agent_id=principal.executing_agent_id,
            connector_id=request.connector_id,
            captured_alias=alias,
        )
        return alias, connection

    def resolve_live(self, claims: QueryCapabilityClaims) -> EngineConnectionConfig:
        principal = claims.principal
        membership = self._workspaces.membership(
            workspace_id=principal.workspace_id, user_id=principal.actor_user_id
        )
        if membership is None or str(membership.role) not in {"owner", "analyst"}:
            raise QueryGatewayDenied("workspace_access_denied")
        resolved = self._resolve_connection(
            workspace_id=principal.workspace_id, connector_id=claims.connector_id
        )
        if resolved is None:
            raise QueryGatewayDenied("resource_access_revoked")
        _alias, connection = resolved
        if principal.executing_agent_id is None:
            raise QueryGatewayDenied("query_capability_scope_invalid")
        self._require_agent_access(
            workspace_id=principal.workspace_id,
            agent_id=principal.executing_agent_id,
            connector_id=claims.connector_id,
            captured_alias=claims.connector_alias,
        )
        if connector_endpoint_digest(connection) != claims.endpoint_digest:
            raise QueryGatewayDenied("connector_endpoint_changed")
        if connection.engine != claims.engine:
            raise QueryGatewayDenied("connector_engine_changed")
        return connection

    def _resolve_connection(
        self, *, workspace_id: str, connector_id: str
    ) -> tuple[str, EngineConnectionConfig] | None:
        try:
            return self._connectors.get_connection_by_id_for_execution(
                workspace_id=workspace_id, connector_id=connector_id
            )
        except SecretStoreError as exc:
            raise QueryGatewayProtocolError(
                "connector_credentials_unavailable", status_code=503
            ) from exc

    def _require_agent_access(
        self,
        *,
        workspace_id: str,
        agent_id: str,
        connector_id: str,
        captured_alias: str,
    ) -> None:
        agent = self._agents.get(agent_id=agent_id, workspace_id=workspace_id)
        if agent is None:
            raise QueryGatewayDenied("agent_profile_not_available")
        allowed_names = set(agent.allowed_database_connector_names)
        if captured_alias in allowed_names:
            return
        for name in allowed_names:
            resolved = self._workspaces.resolve_resource(
                workspace_id=workspace_id,
                resource_kind=WorkspaceResourceKind.DATABASE_CONNECTOR,
                alias=name,
            )
            if resolved is not None and resolved.resource_id == connector_id:
                return
        raise QueryGatewayDenied("agent_database_connector_not_allowed")


class DbtGatewayProfileIssuer:
    def __init__(
        self,
        *,
        resolver: LiveDatabaseCapabilityResolver,
        signer: QueryCapabilitySigner,
        gateway_base_url: str,
        max_ttl_seconds: int = 7200,
    ) -> None:
        self._resolver = resolver
        self._signer = signer
        self._gateway_base_url = gateway_base_url.rstrip("/")
        self._max_ttl_seconds = max_ttl_seconds

    def issue(self, request: IssueDbtProfileRequest) -> IssuedDbtProfile:
        now = datetime.now(tz=UTC)
        if request.expires_at <= now or request.expires_at > now + timedelta(
            seconds=self._max_ttl_seconds
        ):
            raise QueryGatewayDenied("query_capability_expiry_invalid", status_code=422)
        alias, connection = self._resolver.resolve_for_issue(request)
        if connection.engine != "trino":
            raise QueryGatewayDenied("dbt_gateway_connector_must_be_trino", status_code=422)
        claims = QueryCapabilityClaims(
            principal=request.principal,
            connector_id=request.connector_id,
            connector_alias=alias,
            engine=connection.engine,
            endpoint_digest=connector_endpoint_digest(connection),
            issued_at=now,
            expires_at=request.expires_at,
            max_result_bytes=request.max_result_bytes,
            max_concurrency=request.max_concurrency,
        )
        capability = self._signer.issue(claims)
        profile = self._profile(
            profile_name=request.profile_name,
            capability=capability,
            connection=connection,
            threads=request.threads,
        )
        return IssuedDbtProfile(
            connector_id=request.connector_id,
            connector_alias=alias,
            runtime_profile=DbtRuntimeProfile(
                profile_name=request.profile_name,
                profiles_yml=json.dumps(profile, indent=2, sort_keys=True),
                expires_at=request.expires_at,
            ),
        )

    def _profile(
        self,
        *,
        profile_name: str,
        capability: str,
        connection: EngineConnectionConfig,
        threads: int,
    ) -> dict[str, Any]:
        target = urllib.parse.urlsplit(self._gateway_base_url)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("coding_query_gateway_public_url_invalid")
        port = target.port or (443 if target.scheme == "https" else 80)
        output: dict[str, Any] = {
            "type": "trino",
            "method": "none",
            "host": target.hostname,
            "port": port,
            "user": "rhetorica-task",
            "database": connection.catalog or connection.database,
            "schema": connection.schema_name or "default",
            "http_scheme": target.scheme,
            "threads": threads,
            "prepared_statements_enabled": False,
            "http_headers": {"X-Rhetorica-Query-Capability": capability},
        }
        return {profile_name: {"target": "gateway", "outputs": {"gateway": output}}}


class DatabaseQueryCapabilityIssuer:
    """Issue engine-neutral capabilities from existing database connectors."""

    def __init__(
        self,
        *,
        resolver: LiveDatabaseCapabilityResolver,
        signer: QueryCapabilitySigner,
        query_endpoint: str,
        max_ttl_seconds: int = 7200,
    ) -> None:
        self._resolver = resolver
        self._signer = signer
        self._query_endpoint = query_endpoint
        self._max_ttl_seconds = max_ttl_seconds

    def issue(self, request: IssueQueryCapabilityRequest) -> IssuedQueryCapability:
        now = datetime.now(tz=UTC)
        if request.expires_at <= now or request.expires_at > now + timedelta(
            seconds=self._max_ttl_seconds
        ):
            raise QueryGatewayDenied("query_capability_expiry_invalid", status_code=422)
        alias, connection = self._resolver.resolve_for_issue(request)
        claims = QueryCapabilityClaims(
            principal=request.principal,
            connector_id=request.connector_id,
            connector_alias=alias,
            engine=connection.engine,
            endpoint_digest=connector_endpoint_digest(connection),
            issued_at=now,
            expires_at=request.expires_at,
            max_result_bytes=request.max_result_bytes,
            max_concurrency=request.max_concurrency,
        )
        return IssuedQueryCapability(
            connector_id=request.connector_id,
            connector_alias=alias,
            engine=connection.engine,
            capability=self._signer.issue(claims),
            query_endpoint=self._query_endpoint,
        )


@dataclass
class _QueryState:
    scope_key: str
    upstream_next_uri: str | None
    upstream_cancel_uri: str | None
    upstream_base: str
    expires_at: datetime
    result_bytes: int = 0


class QueryHandleRegistry:
    def __init__(self) -> None:
        self._states: dict[str, _QueryState] = {}
        self._active: dict[str, int] = {}
        self._lock = threading.RLock()

    def reserve(self, *, scope_key: str, max_concurrency: int) -> str:
        with self._lock:
            now = datetime.now(tz=UTC)
            expired = [
                handle
                for handle, state in self._states.items()
                if state.scope_key == scope_key and state.expires_at <= now
            ]
            for handle in expired:
                self._states.pop(handle, None)
            if expired:
                remaining = max(0, self._active.get(scope_key, 0) - len(expired))
                if remaining:
                    self._active[scope_key] = remaining
                else:
                    self._active.pop(scope_key, None)
            active = self._active.get(scope_key, 0)
            if active >= max_concurrency:
                raise QueryGatewayDenied("query_concurrency_exceeded", status_code=429)
            self._active[scope_key] = active + 1
            handle = secrets.token_urlsafe(24)
            return handle

    def put(self, handle: str, state: _QueryState) -> None:
        with self._lock:
            self._states[handle] = state

    def get(self, handle: str, *, scope_key: str) -> _QueryState:
        with self._lock:
            state = self._states.get(handle)
            if state is None or state.scope_key != scope_key:
                raise QueryGatewayDenied("query_handle_not_found", status_code=404)
            return state

    def release(self, handle: str, *, scope_key: str) -> None:
        with self._lock:
            state = self._states.pop(handle, None)
            if state is None:
                return
            if state.scope_key != scope_key:
                self._states[handle] = state
                raise QueryGatewayDenied("query_handle_not_found", status_code=404)
            active = self._active.get(scope_key, 0)
            if active <= 1:
                self._active.pop(scope_key, None)
            else:
                self._active[scope_key] = active - 1


class TrinoProtocolGateway:
    _FORBIDDEN_CLIENT_HEADERS = frozenset({
        "authorization",
        "cookie",
        "x-trino-extra-credential",
        "x-trino-original-user",
        "x-trino-path",
        "x-trino-prepared-statement",
        "x-trino-role",
        "x-trino-session",
        "x-trino-transaction-id",
    })

    def __init__(
        self,
        *,
        resolver: LiveDatabaseCapabilityResolver,
        registry: QueryHandleRegistry,
        public_base_url: str,
        client: httpx.Client | None = None,
        sql_policy: StrictTrinoReadOnlyPolicy | None = None,
    ) -> None:
        self._resolver = resolver
        self._registry = registry
        self._public_base_url = public_base_url.rstrip("/")
        self._client = client or httpx.Client(follow_redirects=False)
        self._client_is_injected = client is not None
        self._sql_policy = sql_policy or StrictTrinoReadOnlyPolicy()

    def statement(
        self,
        *,
        claims: QueryCapabilityClaims,
        sql: str,
        client_headers: Mapping[str, str],
    ) -> dict[str, Any]:
        self._check_client_headers(client_headers)
        if claims.engine != "trino":
            raise QueryGatewayDenied("trino_gateway_capability_engine_mismatch", status_code=422)
        decision = self._sql_policy.evaluate(sql)
        if not decision.allowed or decision.sql is None:
            raise QueryGatewayDenied(f"sql_policy_denied:{decision.reason}", status_code=422)
        connection = self._resolver.resolve_live(claims)
        if connection.engine != "trino":
            raise QueryGatewayDenied("trino_gateway_connector_must_be_trino", status_code=422)
        scope_key = self._scope_key(claims)
        handle = self._registry.reserve(scope_key=scope_key, max_concurrency=claims.max_concurrency)
        upstream_base = self._upstream_base(connection)
        state = _QueryState(
            scope_key=scope_key,
            upstream_next_uri=None,
            upstream_cancel_uri=None,
            upstream_base=upstream_base,
            expires_at=claims.expires_at,
        )
        self._registry.put(handle, state)
        try:
            response = self._send(
                "POST",
                f"{upstream_base}/v1/statement",
                connection=connection,
                content=decision.sql.encode("utf-8"),
                headers={
                    **self._upstream_headers(connection, claims),
                    **self._client_capabilities(client_headers),
                },
                auth=self._auth(connection) or httpx.USE_CLIENT_DEFAULT,
                timeout=self._timeout(claims),
            )
            return self._consume_response(handle, claims=claims, response=response)
        except Exception:
            self._registry.release(handle, scope_key=scope_key)
            raise

    def poll(self, *, claims: QueryCapabilityClaims, handle: str) -> dict[str, Any]:
        connection = self._resolver.resolve_live(claims)
        scope_key = self._scope_key(claims)
        state = self._registry.get(handle, scope_key=scope_key)
        if state.upstream_next_uri is None:
            raise QueryGatewayDenied("query_handle_not_found", status_code=404)
        self._require_upstream_uri(state.upstream_next_uri, state.upstream_base)
        try:
            response = self._send(
                "GET",
                state.upstream_next_uri,
                connection=connection,
                headers=self._upstream_headers(connection, claims),
                auth=self._auth(connection) or httpx.USE_CLIENT_DEFAULT,
                timeout=self._timeout(claims),
            )
            return self._consume_response(handle, claims=claims, response=response)
        except Exception:
            self._cancel_best_effort(handle, claims)
            raise

    def cancel(self, *, claims: QueryCapabilityClaims, handle: str) -> None:
        connection = self._resolver.resolve_live(claims)
        scope_key = self._scope_key(claims)
        state = self._registry.get(handle, scope_key=scope_key)
        uri = state.upstream_cancel_uri or state.upstream_next_uri
        try:
            if uri is not None:
                self._require_upstream_uri(uri, state.upstream_base)
                response = self._send(
                    "DELETE",
                    uri,
                    connection=connection,
                    headers=self._upstream_headers(connection, claims),
                    auth=self._auth(connection) or httpx.USE_CLIENT_DEFAULT,
                    timeout=self._timeout(claims),
                )
                self._require_response(response)
        finally:
            self._registry.release(handle, scope_key=scope_key)

    def _consume_response(
        self,
        handle: str,
        *,
        claims: QueryCapabilityClaims,
        response: httpx.Response,
    ) -> dict[str, Any]:
        self._require_response(response)
        try:
            page = response.json()
        except ValueError as exc:
            raise QueryGatewayProtocolError("trino_response_not_json") from exc
        if not isinstance(page, dict):
            raise QueryGatewayProtocolError("trino_response_invalid")
        if self._is_spooled(page):
            self._cancel_best_effort(handle, claims)
            raise QueryGatewayProtocolError("trino_spooling_not_supported", status_code=422)
        scope_key = self._scope_key(claims)
        state = self._registry.get(handle, scope_key=scope_key)
        next_uri = page.get("nextUri")
        cancel_uri = page.get("partialCancelUri")
        if next_uri is not None:
            self._require_upstream_uri(str(next_uri), state.upstream_base)
        if cancel_uri is not None:
            self._require_upstream_uri(str(cancel_uri), state.upstream_base)
        state.upstream_next_uri = str(next_uri) if next_uri else None
        state.upstream_cancel_uri = str(cancel_uri) if cancel_uri else None
        state.result_bytes += len(
            json.dumps(page.get("data"), separators=(",", ":"), default=str).encode("utf-8")
        )
        if state.result_bytes > claims.max_result_bytes:
            self._cancel_best_effort(handle, claims)
            raise QueryGatewayDenied("query_result_budget_exceeded", status_code=413)
        page["id"] = handle
        page["infoUri"] = f"{self._public_base_url}/v1/query/{handle}"
        if state.upstream_next_uri:
            page["nextUri"] = f"{self._public_base_url}/v1/query/{handle}"
        else:
            page.pop("nextUri", None)
        if state.upstream_cancel_uri or state.upstream_next_uri:
            page["partialCancelUri"] = f"{self._public_base_url}/v1/query/{handle}"
        else:
            page.pop("partialCancelUri", None)
        if page.get("error") is not None or state.upstream_next_uri is None:
            self._registry.release(handle, scope_key=scope_key)
        return page

    def _cancel_best_effort(self, handle: str, claims: QueryCapabilityClaims) -> None:
        scope_key = self._scope_key(claims)
        try:
            state = self._registry.get(handle, scope_key=scope_key)
            uri = state.upstream_cancel_uri or state.upstream_next_uri
            if uri is not None:
                self._require_upstream_uri(uri, state.upstream_base)
                connection = self._resolver.resolve_live(claims)
                self._send(
                    "DELETE",
                    uri,
                    connection=connection,
                    headers=self._upstream_headers(connection, claims),
                    auth=self._auth(connection) or httpx.USE_CLIENT_DEFAULT,
                    timeout=min(2.0, self._timeout(claims)),
                )
        except Exception as exc:
            _ignored_cancel_error = exc
        finally:
            self._registry.release(handle, scope_key=scope_key)

    # Result-format only; without it timestamp(p) columns break the dbt-trino client.
    _FORWARDED_CLIENT_CAPABILITIES = frozenset({"PARAMETRIC_DATETIME"})

    @classmethod
    def _client_capabilities(cls, headers: Mapping[str, str]) -> dict[str, str]:
        requested = next(
            (
                value
                for name, value in headers.items()
                if name.lower() == "x-trino-client-capabilities"
            ),
            "",
        )
        capabilities = [
            item.strip()
            for item in requested.split(",")
            if item.strip() in cls._FORWARDED_CLIENT_CAPABILITIES
        ]
        return {"X-Trino-Client-Capabilities": ",".join(capabilities)} if capabilities else {}

    @classmethod
    def _check_client_headers(cls, headers: Mapping[str, str]) -> None:
        for name, value in headers.items():
            lowered = name.lower()
            if lowered not in cls._FORBIDDEN_CLIENT_HEADERS:
                continue
            normalized = value.strip()
            # dbt-trino sends empty session/prepared/role headers and the Trino
            # protocol's default transaction marker. They carry no authority.
            if not normalized or (
                lowered == "x-trino-transaction-id" and normalized.upper() == "NONE"
            ):
                continue
            raise QueryGatewayDenied(f"trino_client_header_forbidden:{lowered}", status_code=422)

    @staticmethod
    def _scope_key(claims: QueryCapabilityClaims) -> str:
        principal = claims.principal
        return ":".join((
            principal.workspace_id,
            principal.run_id,
            principal.task_id or "",
            claims.connector_id,
        ))

    @staticmethod
    def _upstream_base(connection: EngineConnectionConfig) -> str:
        scheme = "https" if connection.secure else "http"
        return f"{scheme}://{connection.host}:{connection.port}"

    @staticmethod
    def _auth(connection: EngineConnectionConfig) -> httpx.BasicAuth | None:
        if connection.password is None:
            return None
        return httpx.BasicAuth(connection.username, connection.password)

    @staticmethod
    def _timeout(claims: QueryCapabilityClaims) -> float:
        remaining = (claims.expires_at - datetime.now(tz=UTC)).total_seconds()
        if remaining <= 0:
            raise QueryGatewayDenied("query_capability_expired", status_code=401)
        return max(0.1, remaining)

    @classmethod
    def _upstream_headers(
        cls, connection: EngineConnectionConfig, claims: QueryCapabilityClaims
    ) -> dict[str, str]:
        remaining = max(1, int((claims.expires_at - datetime.now(tz=UTC)).total_seconds()))
        session: dict[str, str] = {}
        for key, value in connection.extra_settings.items():
            if key in {"http_headers", "set_query_max_run_time"} or key.startswith("metadata_"):
                continue
            if isinstance(value, str | int | float | bool):
                session[key] = str(value)
        if connection.extra_settings.get("set_query_max_run_time", True) is not False:
            session["query_max_run_time"] = f"{remaining}s"
        headers = {
            "X-Trino-User": connection.username,
            "X-Trino-Source": "rhetorica-coding-gateway",
            "X-Trino-Session": ",".join(
                f"{name}={urllib.parse.quote(value)}" for name, value in sorted(session.items())
            ),
        }
        if connection.catalog:
            headers["X-Trino-Catalog"] = connection.catalog
        if connection.schema_name:
            headers["X-Trino-Schema"] = connection.schema_name
        configured = connection.extra_settings.get("http_headers")
        if isinstance(configured, dict):
            for raw_name, raw_value in configured.items():
                name = str(raw_name).strip()
                lowered = name.lower()
                if (
                    not name
                    or lowered in cls._FORBIDDEN_CLIENT_HEADERS
                    or lowered == "content-length"
                    or lowered.startswith("x-trino-")
                ):
                    continue
                headers[name] = str(raw_value)
        if connection.extra_credentials:
            credentials: list[str] = []
            for name, credential_value in sorted(connection.extra_credentials.items()):
                if credential_value is None:
                    raise QueryGatewayDenied(f"trino_extra_credential_missing:{name}")
                credentials.append(f"{name}={urllib.parse.quote_plus(str(credential_value))}")
            headers["X-Trino-Extra-Credential"] = ", ".join(credentials)
        return headers

    def _send(
        self,
        method: str,
        url: str,
        *,
        connection: EngineConnectionConfig,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            if connection.verify or self._client_is_injected:
                return self._client.request(method, url, **kwargs)
            # Connector TLS verification is an administrator-owned setting; the
            # sandbox cannot influence it.
            with httpx.Client(verify=False, follow_redirects=False) as client:  # noqa: S501
                return client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise QueryGatewayProtocolError("trino_upstream_unavailable") from exc

    @staticmethod
    def _require_upstream_uri(uri: str, expected_base: str) -> None:
        parsed = urllib.parse.urlsplit(uri)
        expected = urllib.parse.urlsplit(expected_base)
        if (
            parsed.scheme != expected.scheme
            or parsed.hostname != expected.hostname
            or TrinoProtocolGateway._effective_port(parsed)
            != TrinoProtocolGateway._effective_port(expected)
            or not parsed.path.startswith("/v1/statement/")
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise QueryGatewayProtocolError("trino_upstream_uri_forbidden", status_code=422)

    @staticmethod
    def _effective_port(parsed: urllib.parse.SplitResult) -> int | None:
        if parsed.port is not None:
            return parsed.port
        return {"http": 80, "https": 443}.get(parsed.scheme)

    @staticmethod
    def _require_response(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            raise QueryGatewayProtocolError("trino_redirect_forbidden", status_code=422)
        if response.status_code >= 400:
            raise QueryGatewayProtocolError(
                f"trino_upstream_http_{response.status_code}", status_code=502
            )

    @staticmethod
    def _is_spooled(page: dict[str, Any]) -> bool:
        if any(key in page for key in ("spooledEncoding", "spooling", "segments")):
            return True
        data = page.get("data")
        return isinstance(data, list) and any(
            isinstance(item, dict) and ({"uri", "ackUri", "metadata"} & set(item)) for item in data
        )
