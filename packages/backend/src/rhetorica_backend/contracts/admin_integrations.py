from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

ScalarSetting = str | int | float | bool
ApiKeyValue = Annotated[str, Field(max_length=4096)]
LlmMaxTokensParameter = Literal["max_tokens", "max_completion_tokens"]
LlmReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]

RagProvider = Literal["qdrant"]
RagDistance = Literal["cosine", "dot", "euclid"]
RagVerificationStatus = Literal["pending", "verified", "rejected"]
DocumentationProvider = Literal["docsy_hugo"]
DocumentationAuthType = Literal["none", "bearer", "basic"]
DocumentationContentFormat = Literal["markdown", "json", "html"]
GitLabProvider = Literal["gitlab"]
MattermostProvider = Literal["mattermost"]
AirflowProvider = Literal["airflow"]
AirflowAuthType = Literal["basic"]
KubernetesProvider = Literal["kubernetes"]
KubernetesAuthType = Literal["bearer"]
VictoriaMetricsProvider = Literal["victoriametrics"]
VictoriaMetricsAuthType = Literal["none", "bearer"]
McpProvider = Literal["jira", "confluence", "gitlab", "generic"]
McpTransport = Literal["streamable-http"]
McpSecretHeaderMode = Literal["raw", "bearer"]
MAX_AMBIENT_DECISION_PROMPT_CHARS = 20_000


class LlmConnectionConfig(BaseModel):
    """Connection details for an explicit OpenAI-compatible API endpoint."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=1024)
    model: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    api_keys: list[ApiKeyValue] | None = Field(default=None, max_length=50)
    retained_api_key_indexes: list[int] | None = Field(default=None, max_length=50)
    endpoint_path: str = Field(default="/responses", min_length=1, max_length=255)
    verify_tls: bool = True
    temperature: float | None = Field(default=0.1, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: LlmReasoningEffort | None = None
    max_tokens: int | None = Field(default=1_200, ge=1, le=32_000)
    max_tokens_parameter: LlmMaxTokensParameter = "max_tokens"
    headers: dict[str, str] = Field(default_factory=dict)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)
    phase_capable: None = Field(
        default=None,
        deprecated=True,
        description="Phase overrides are unsupported; only null is accepted.",
    )

    _connector_id: str | None = PrivateAttr(default=None)
    _credential_set_version: int = PrivateAttr(default=1)
    _selected_api_key_index: int | None = PrivateAttr(default=None)
    _credential_rebind: Callable[[int, int], bool] | None = PrivateAttr(default=None)

    @property
    def store(self) -> bool | None:
        """Optional provider-side response storage; independent of local history."""
        value = self.extra_settings.get("store")
        return value if isinstance(value, bool) else None

    @property
    def connector_id(self) -> str | None:
        """Internal connector identity used for conversation credential affinity."""
        return self._connector_id

    @property
    def credential_set_version(self) -> int:
        """Internal version of the configured API-key set."""
        return self._credential_set_version

    @property
    def selected_api_key_index(self) -> int | None:
        """Index selected for the current conversation/turn, if any."""
        return self._selected_api_key_index

    def configure_credential_binding(
        self,
        *,
        connector_id: str,
        credential_set_version: int,
        selected_api_key_index: int,
        rebind: Callable[[int, int], bool] | None = None,
    ) -> None:
        """Attach internal credential-affinity state without exposing it in contracts."""
        self._connector_id = connector_id
        self._credential_set_version = credential_set_version
        self._selected_api_key_index = selected_api_key_index
        self._credential_rebind = rebind

    def configure_connector_identity(
        self,
        *,
        connector_id: str,
        credential_set_version: int,
    ) -> None:
        """Attach connector metadata used by internal conversation binding."""
        self._connector_id = connector_id
        self._credential_set_version = credential_set_version

    def commit_credential_retry(self, *, selected_api_key_index: int, expected_index: int) -> bool:
        """Persist a successful retry and update the in-memory selection."""
        persisted = True
        if self._credential_rebind is not None:
            persisted = self._credential_rebind(
                selected_api_key_index,
                expected_index,
            )
        self._selected_api_key_index = selected_api_key_index
        if self.api_keys and 0 <= selected_api_key_index < len(self.api_keys):
            self.api_key = self.api_keys[selected_api_key_index]
        return persisted

    @field_validator("api_keys", mode="before")
    @classmethod
    def _normalize_api_keys(cls, value: Any) -> Any:
        if value is None:
            return None
        items = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("retained_api_key_indexes")
    @classmethod
    def _normalize_retained_api_key_indexes(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        normalized: list[int] = []
        seen: set[int] = set()
        for item in value:
            if item < 0 or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    @field_validator("extra_settings")
    @classmethod
    def _validate_store(cls, value: dict[str, ScalarSetting]) -> dict[str, ScalarSetting]:
        if "store" in value and not isinstance(value["store"], bool):
            raise ValueError("store_must_be_boolean")
        return value

    @field_validator("extra_settings")
    @classmethod
    def _validate_runtime_context_role(
        cls,
        value: dict[str, ScalarSetting],
    ) -> dict[str, ScalarSetting]:
        runtime_context_role = value.get("runtime_context_role")
        if runtime_context_role is not None and runtime_context_role not in {
            "developer",
            "user",
        }:
            raise ValueError("runtime_context_role_must_be_developer_or_user")
        return value

    @model_validator(mode="after")
    def _validate_native_compaction(self) -> LlmConnectionConfig:
        enabled = self.extra_settings.get("native_compaction_enabled", False)
        threshold = self.extra_settings.get("native_compaction_threshold_tokens")
        if not isinstance(enabled, bool):
            raise ValueError("native_compaction_enabled_must_be_boolean")  # noqa: TRY004
        if threshold is not None and (
            isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1
        ):
            raise ValueError("native_compaction_threshold_tokens_must_be_positive_integer")
        if enabled:
            if not self.endpoint_path.rstrip("/").endswith("/responses"):
                raise ValueError("native_compaction_requires_responses_endpoint")
            if threshold is None:
                raise ValueError("native_compaction_threshold_tokens_required")
        return self


class UpsertLlmConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    is_default: bool | None = None
    connection: LlmConnectionConfig

    @model_validator(mode="after")
    def _default_connector_must_be_active(self) -> UpsertLlmConnectorRequest:
        if self.is_default is True and not self.is_active:
            raise ValueError("default_llm_connector_must_be_active")
        return self


class LlmConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    is_default: bool = False
    connection: LlmConnectionConfig
    has_api_key: bool
    api_key_count: int = 0
    capability_profile: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class RagConnectionConfig(BaseModel):
    """Connection details for Qdrant-backed RAG insights storage."""

    model_config = ConfigDict(extra="forbid")

    provider: RagProvider = "qdrant"
    base_url: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    collection_name: str = Field(default="rag_insights", min_length=1, max_length=255)
    vector_size: int = Field(default=384, ge=8, le=4096)
    distance: RagDistance = "cosine"
    prefer_grpc: bool = False
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1, max_length=255)
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)


class UpsertRagConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: RagConnectionConfig


class RagConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: RagConnectionConfig
    has_api_key: bool
    created_at: datetime
    updated_at: datetime


class DocumentationConnectionConfig(BaseModel):
    """Connection details for Docsy/Hugo documentation retrieval."""

    model_config = ConfigDict(extra="forbid")

    provider: DocumentationProvider = "docsy_hugo"
    base_url: str = Field(min_length=1, max_length=1024)
    index_url: str | None = Field(default=None, max_length=1024)
    content_format: DocumentationContentFormat = "markdown"
    username: str | None = Field(default=None, max_length=255)
    api_token: str | None = Field(default=None, max_length=4096)
    usage_description: str | None = Field(default=None, max_length=1000)
    auth_type: DocumentationAuthType = "none"
    verify_tls: bool = True
    include_paths: list[str] = Field(default_factory=list, max_length=100)
    exclude_paths: list[str] = Field(default_factory=list, max_length=100)
    sync_enabled: bool = True
    sync_interval_minutes: int = Field(default=1440, ge=5, le=43_200)
    qdrant_base_url: str = Field(default="http://qdrant:6333", min_length=1, max_length=1024)
    qdrant_api_key: str | None = Field(default=None, max_length=4096)
    qdrant_collection_name: str = Field(
        default="documentation_chunks",
        min_length=1,
        max_length=255,
    )
    qdrant_prefer_grpc: bool = False
    vector_size: int = Field(default=1024, ge=8, le=4096)
    embedding_base_url: str | None = Field(default=None, max_length=1024)
    embedding_api_key: str | None = Field(default=None, max_length=4096)
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1, max_length=255)
    embedding_endpoint_path: str = Field(default="/v1/embeddings", min_length=1, max_length=255)
    embedding_timeout_seconds: int = Field(default=120, ge=1, le=600)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url", "index_url", "qdrant_base_url", "embedding_base_url")
    @classmethod
    def _normalize_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("include_paths", "exclude_paths")
    @classmethod
    def _normalize_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized


class UpsertDocumentationConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    visible_to_default_assistant: bool = True
    connection: DocumentationConnectionConfig


class DocumentationConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    visible_to_default_assistant: bool = True
    connection: DocumentationConnectionConfig
    has_api_token: bool
    has_embedding_api_key: bool
    has_qdrant_api_key: bool
    last_sync_status: str | None = None
    last_sync_at: datetime | None = None
    next_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentationSyncRunItem(BaseModel):
    id: str
    connector_id: str
    workspace_id: str
    connector_name: str
    status: str
    trigger: str
    pages_seen: int = 0
    pages_updated: int = 0
    chunks_indexed: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class DocumentationSyncResponse(BaseModel):
    connector_id: str
    task_id: str | None = None
    status: str


class GitLabConnectionConfig(BaseModel):
    """Connection details for read-only GitLab content retrieval."""

    model_config = ConfigDict(extra="forbid")

    provider: GitLabProvider = "gitlab"
    base_url: str = Field(min_length=1, max_length=1024)
    api_token: str | None = Field(default=None, max_length=4096)
    use_when: str = Field(min_length=1, max_length=1000)
    verify_tls: bool = True
    default_project_paths: list[str] = Field(default_factory=list, max_length=100)
    default_group_paths: list[str] = Field(default_factory=list, max_length=100)
    search_limit: int = Field(default=5, ge=1, le=10)
    read_max_tokens: int = Field(default=1600, ge=200, le=4000)
    coding_archive_max_files: int = Field(default=20_000, ge=1, le=100_000)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_gitlab(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("default_project_paths", "default_group_paths")
    @classmethod
    def _normalize_paths_for_gitlab(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip().strip("/")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized


class UpsertGitLabConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: GitLabConnectionConfig


class GitLabConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: GitLabConnectionConfig
    has_api_token: bool
    created_at: datetime
    updated_at: datetime


class McpSecretHeaderInput(BaseModel):
    """Sensitive MCP request header supplied by an admin."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=4096)
    mode: McpSecretHeaderMode = "raw"

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return value.strip()


class McpConnectionConfig(BaseModel):
    """Connection details for remote MCP servers exposed as runtime tools."""

    model_config = ConfigDict(extra="forbid")

    provider: McpProvider = "generic"
    transport: McpTransport = "streamable-http"
    url: str = Field(min_length=1, max_length=1024)
    verify_tls: bool = True
    headers: dict[str, str] = Field(default_factory=dict, max_length=100)
    secret_headers: list[McpSecretHeaderInput] | None = Field(default=None, max_length=50)
    enabled_groups: str | None = Field(default=None, max_length=512)
    allowed_tool_names: list[str] = Field(default_factory=list, max_length=500)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    tool_name_prefix: str | None = Field(default=None, min_length=1, max_length=64)
    llm_connector_name: str | None = Field(default=None, max_length=128)
    usage_description: str | None = Field(default=None, max_length=1000)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/") or "/mcp"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("headers")
    @classmethod
    def _normalize_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, raw in value.items():
            name = key.strip()
            val = raw.strip() if isinstance(raw, str) else str(raw)
            if name and val:
                normalized[name] = val
        return normalized

    @field_validator("allowed_tool_names")
    @classmethod
    def _normalize_allowed_tool_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            name = raw.strip()
            if not name:
                continue
            if len(name) > 255:
                raise ValueError("mcp_allowed_tool_name_too_long")
            if name not in seen:
                normalized.append(name)
                seen.add(name)
        return normalized

    @field_validator("tool_name_prefix")
    @classmethod
    def _normalize_tool_name_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if not candidate.replace("_", "").replace("-", "").isalnum():
            raise ValueError("mcp_tool_name_prefix_invalid")
        return candidate


class UpsertMcpConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    visible_to_external_mcp: bool = False
    connection: McpConnectionConfig


class McpConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    visible_to_external_mcp: bool = False
    connection: McpConnectionConfig
    has_secret_headers: bool
    secret_header_names: list[str] = Field(default_factory=list)
    tool_count: int = 0
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class McpToolCatalogItem(BaseModel):
    id: str
    connector_id: str
    workspace_id: str
    connector_name: str
    remote_tool_name: str
    exposed_tool_name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class McpToolSyncResponse(BaseModel):
    connector_id: str
    synced: bool
    tool_count: int
    status: str
    error_message: str | None = None


class AirflowConnectionConfig(BaseModel):
    """Connection details for read-only Airflow REST API access."""

    model_config = ConfigDict(extra="forbid")

    provider: AirflowProvider = "airflow"
    base_url: str = Field(min_length=1, max_length=1024)
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    use_when: str = Field(min_length=1, max_length=1000)
    auth_type: AirflowAuthType = "basic"
    verify_tls: bool = True
    api_path: str = Field(default="/api/v1", min_length=1, max_length=255)
    dag_limit: int = Field(default=25, ge=1, le=100)
    dag_run_limit: int = Field(default=20, ge=1, le=100)
    task_limit: int = Field(default=50, ge=1, le=200)
    log_max_chars: int = Field(default=8000, ge=1000, le=50000)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_airflow(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("api_path")
    @classmethod
    def _normalize_api_path(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            return "/api/v1"
        normalized = f"/{raw.lstrip('/')}".rstrip("/")
        return normalized or "/api/v1"


class UpsertAirflowConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    connection: AirflowConnectionConfig


class AirflowConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    connection: AirflowConnectionConfig
    has_password: bool
    created_at: datetime
    updated_at: datetime


class KubernetesConnectionConfig(BaseModel):
    """Connection details for read-only Kubernetes API access."""

    model_config = ConfigDict(extra="forbid")

    provider: KubernetesProvider = "kubernetes"
    base_url: str = Field(min_length=1, max_length=1024)
    bearer_token: str | None = Field(default=None, max_length=4096)
    use_when: str = Field(min_length=1, max_length=1000)
    auth_type: KubernetesAuthType = "bearer"
    verify_tls: bool = True
    namespace_allowlist: list[str] = Field(default_factory=list, min_length=1, max_length=100)
    default_limit: int = Field(default=25, ge=1, le=200)
    max_limit: int = Field(default=100, ge=1, le=500)
    log_max_chars: int = Field(default=8000, ge=1000, le=50000)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_kubernetes(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("namespace_allowlist")
    @classmethod
    def _normalize_namespace_allowlist(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        if not normalized:
            raise ValueError("kubernetes_namespace_allowlist_required")
        return normalized


class UpsertKubernetesConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    visible_to_default_assistant: bool = False
    connection: KubernetesConnectionConfig


class KubernetesConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    visible_to_default_assistant: bool = False
    connection: KubernetesConnectionConfig
    has_bearer_token: bool
    created_at: datetime
    updated_at: datetime


class VictoriaMetricsTemplateConfig(BaseModel):
    """Admin-owned PromQL template exposed to the LLM as an intent."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    use_when: str = Field(min_length=1, max_length=1000)
    query_template: str = Field(min_length=1, max_length=4000)
    mode: Literal["instant", "range", "both"] = "both"
    allowed_filters: list[str] = Field(default_factory=list, max_length=50)
    default_step: str = Field(default="60s", min_length=1, max_length=32)
    max_range_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    unit: str | None = Field(default=None, max_length=64)
    result_limit: int = Field(default=20, ge=1, le=200)

    @field_validator("template_id")
    @classmethod
    def _normalize_template_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("victoriametrics_template_id_required")
        return normalized

    @field_validator("allowed_filters")
    @classmethod
    def _normalize_allowed_filters(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized


class VictoriaMetricsConnectionConfig(BaseModel):
    """Connection details for VictoriaMetrics Prometheus-compatible API access."""

    model_config = ConfigDict(extra="forbid")

    provider: VictoriaMetricsProvider = "victoriametrics"
    base_url: str = Field(min_length=1, max_length=1024)
    bearer_token: str | None = Field(default=None, max_length=4096)
    use_when: str = Field(min_length=1, max_length=1000)
    auth_type: VictoriaMetricsAuthType = "none"
    verify_tls: bool = True
    query_path: str = Field(default="/prometheus/api/v1/query", min_length=1, max_length=255)
    query_range_path: str = Field(
        default="/prometheus/api/v1/query_range",
        min_length=1,
        max_length=255,
    )
    templates: list[VictoriaMetricsTemplateConfig] = Field(default_factory=list, max_length=100)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    max_series: int = Field(default=20, ge=1, le=200)
    max_datapoints: int = Field(default=300, ge=1, le=5000)
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_victoriametrics(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("query_path", "query_range_path")
    @classmethod
    def _normalize_vm_api_path(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("victoriametrics_api_path_required")
        return f"/{raw.lstrip('/')}".rstrip("/")


class UpsertVictoriaMetricsConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    visible_to_default_assistant: bool = False
    connection: VictoriaMetricsConnectionConfig


class VictoriaMetricsConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    visible_to_default_assistant: bool = False
    connection: VictoriaMetricsConnectionConfig
    has_bearer_token: bool
    created_at: datetime
    updated_at: datetime


class MattermostConnectionConfig(BaseModel):
    """Connection details for Mattermost/Tag bot transport."""

    model_config = ConfigDict(extra="forbid")

    provider: MattermostProvider = "mattermost"
    base_url: str = Field(default="https://mattermost.example.com", min_length=1, max_length=1024)
    bot_token: str | None = Field(default=None, max_length=4096, repr=False)
    bot_username: str | None = Field(default=None, max_length=255)
    verify_tls: bool = True
    listen_enabled: bool = True
    send_sql_enabled: bool = False
    ambient_listen_enabled: bool = False
    ambient_channel_ids: list[str] = Field(default_factory=list, max_length=100)
    ambient_decision_prompt: str | None = Field(
        default=None,
        max_length=MAX_AMBIENT_DECISION_PROMPT_CHARS,
    )
    extra_settings: dict[str, ScalarSetting] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url_for_mattermost(cls, value: str) -> str:
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return raw
        path = (parsed.path or "").rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @field_validator("bot_username")
    @classmethod
    def _normalize_bot_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lstrip("@")
        return normalized or None

    @field_validator("ambient_channel_ids")
    @classmethod
    def _normalize_ambient_channel_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("ambient_decision_prompt")
    @classmethod
    def _normalize_ambient_decision_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UpsertMattermostConnectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    is_active: bool = True
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    connection: MattermostConnectionConfig


class MattermostConnectorItem(BaseModel):
    id: str
    name: str
    workspace_id: str
    is_active: bool = True
    agent_id: str | None = None
    connection: MattermostConnectionConfig
    has_bot_token: bool
    created_at: datetime
    updated_at: datetime


class RagInsightReviewItem(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    message_id: str
    execution_id: str | None = None
    summary: str
    article_markdown: str | None = None
    answer_excerpt: str
    generated_sql: str | None = None
    context_tables: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    llm_confidence: float = Field(ge=0, le=1)
    llm_reason: str
    likes_count: int = Field(default=0, ge=0)
    verification_status: RagVerificationStatus
    human_verified: bool
    verified_by: str | None = None
    verified_at: str | None = None
    created_at: str
    updated_at: str


class RagInsightReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1, max_length=128)
    verified_by: str = Field(default="admin", min_length=1, max_length=255)


class RagInsightReviewActionResponse(BaseModel):
    record_id: str
    verification_status: RagVerificationStatus
    verified_by: str
    verified_at: str


class DeleteConnectorResponse(BaseModel):
    connector_id: str
    deleted: bool
