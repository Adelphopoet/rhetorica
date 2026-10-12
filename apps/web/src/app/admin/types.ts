import type { CodingTeamDetail } from "@/features/coding/codingApi";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import type { AgentProfile } from "@/features/agents/agentsApi";

export type DatabaseExtraSetting =
  | string
  | number
  | boolean
  | Record<string, string | number | boolean>;

export type DatabaseConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_password: boolean;
  has_extra_credentials?: boolean;
  connection: {
    engine: "clickhouse" | "trino" | "greenplum" | "impala";
    host: string;
    port: number;
    username: string;
    usage_description: string | null;
    database: string | null;
    catalog: string | null;
    schema: string | null;
    secure: boolean;
    verify: boolean;
    extra_settings: Record<string, DatabaseExtraSetting>;
    extra_credentials?: Record<string, null> | null;
  };
  created_at: string;
  updated_at: string;
};

export type LlmConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  is_default: boolean;
  capability_profile?: Record<string, unknown> | null;
  has_api_key: boolean;
  api_key_count: number;
  connection: {
    base_url: string;
    model: string;
    api_keys?: string[] | null;
    retained_api_key_indexes?: number[] | null;
    endpoint_path: string;
    verify_tls: boolean;
    temperature: number | null;
    top_p?: number | null;
    reasoning_effort?: "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | null;
    max_tokens: number | null;
    max_tokens_parameter?: "max_tokens" | "max_completion_tokens";
    headers: Record<string, string>;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type DocumentationConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  visible_to_default_assistant: boolean;
  has_api_token: boolean;
  has_embedding_api_key: boolean;
  has_qdrant_api_key: boolean;
  last_sync_status: string | null;
  last_sync_at: string | null;
  next_sync_at: string | null;
  connection: {
    provider: "docsy_hugo";
    base_url: string;
    index_url: string | null;
    content_format: "markdown" | "json" | "html";
    username: string | null;
    usage_description: string | null;
    auth_type: "none" | "bearer" | "basic";
    verify_tls: boolean;
    include_paths: string[];
    exclude_paths: string[];
    sync_enabled: boolean;
    sync_interval_minutes: number;
    qdrant_base_url: string;
    qdrant_collection_name: string;
    qdrant_prefer_grpc: boolean;
    vector_size: number;
    embedding_base_url: string | null;
    embedding_model: string;
    embedding_endpoint_path: string;
    embedding_timeout_seconds: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type GitLabConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_api_token: boolean;
  connection: {
    provider: "gitlab";
    base_url: string;
    use_when: string;
    verify_tls: boolean;
    default_project_paths: string[];
    default_group_paths: string[];
    search_limit: number;
    read_max_tokens: number;
    coding_archive_max_files: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type GitLabWriteScope = {
  connector_id: string;
  project_id: string;
  target_branches: string[];
  allowed_paths: string[];
};

export type AgentDelegationGrant = {
  subagent_id: string;
  gitlab_write_scopes: GitLabWriteScope[];
};

export type GovernedAgentPolicy = {
  workspace_id: string;
  agent_id?: string;
  revision: number;
  gitlab_write_scopes: GitLabWriteScope[];
  allowed_subagent_ids: string[];
  delegation_grants: AgentDelegationGrant[];
  max_iterations: number;
  max_runtime_seconds: number;
};

export type CodingEnvironmentProfile = {
  id: string;
  revision: number;
  image_digest: string;
  runtime_class_name: string;
  tool_versions: Record<string, string>;
  max_runtime_seconds: number;
  cpu_limit: string;
  memory_limit: string;
  disk_limit_bytes: number;
  pid_limit: number;
  output_limit_bytes: number;
  software_manifest_digest: string;
  dependency_sources: string[];
  pip_dependencies?: string[];
  created_at: string;
};

export type McpConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  visible_to_external_mcp: boolean;
  has_secret_headers: boolean;
  secret_header_names: string[];
  tool_count: number;
  last_sync_status: string | null;
  last_sync_error: string | null;
  last_sync_at: string | null;
  connection: {
    provider: "jira" | "confluence" | "gitlab" | "generic";
    transport: "streamable-http";
    url: string;
    verify_tls: boolean;
    headers: Record<string, string>;
    secret_headers?: Array<{ name: string; value: string; mode: "raw" | "bearer" }> | null;
    enabled_groups: string | null;
    allowed_tool_names?: string[];
    timeout_seconds: number;
    tool_name_prefix: string | null;
    llm_connector_name: string | null;
    usage_description: string | null;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type AirflowConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_password: boolean;
  connection: {
    provider: "airflow";
    base_url: string;
    username: string;
    use_when: string;
    auth_type: "basic";
    verify_tls: boolean;
    api_path: string;
    dag_limit: number;
    dag_run_limit: number;
    task_limit: number;
    log_max_chars: number;
    timeout_seconds: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type KubernetesConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  visible_to_default_assistant: boolean;
  has_bearer_token: boolean;
  connection: {
    provider: "kubernetes";
    base_url: string;
    use_when: string;
    auth_type: "bearer";
    verify_tls: boolean;
    namespace_allowlist: string[];
    default_limit: number;
    max_limit: number;
    log_max_chars: number;
    timeout_seconds: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type VictoriaMetricsTemplateConfig = {
  template_id: string;
  title: string;
  use_when: string;
  query_template: string;
  mode: "instant" | "range" | "both";
  allowed_filters: string[];
  default_step: string;
  max_range_seconds: number;
  unit: string | null;
  result_limit: number;
};

export type VictoriaMetricsConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  visible_to_default_assistant: boolean;
  has_bearer_token: boolean;
  connection: {
    provider: "victoriametrics";
    base_url: string;
    use_when: string;
    auth_type: "none" | "bearer";
    verify_tls: boolean;
    query_path: string;
    query_range_path: string;
    templates: VictoriaMetricsTemplateConfig[];
    timeout_seconds: number;
    max_series: number;
    max_datapoints: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type MattermostConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  agent_id: string | null;
  has_bot_token: boolean;
  connection: {
    provider: "mattermost";
    base_url: string;
    bot_username: string | null;
    verify_tls: boolean;
    listen_enabled: boolean;
    send_sql_enabled: boolean;
    ambient_listen_enabled: boolean;
    ambient_channel_ids: string[];
    ambient_decision_prompt: string | null;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type RagInsightReviewItem = {
  id: string;
  workspace_id: string;
  conversation_id: string;
  message_id: string;
  execution_id: string | null;
  summary: string;
  article_markdown: string | null;
  answer_excerpt: string;
  generated_sql: string | null;
  context_tables: string[];
  metadata_sources: string[];
  keywords: string[];
  llm_confidence: number;
  llm_reason: string;
  likes_count: number;
  verification_status: "pending" | "verified" | "rejected";
  human_verified: boolean;
  verified_by: string | null;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceSkillItem = {
  id: string;
  workspace_id: string;
  name: string;
  capability_family: "chat_instruction";
  engine_scope: "clickhouse" | "trino" | "greenplum" | "impala" | "any";
  priority: number;
  source_kind: "github_markdown_url" | "gitlab_bundle" | "manual_markdown";
  source_url: string | null;
  status: "active" | "inactive";
  validation_status: "valid" | "invalid" | "pending";
  visible_to_default_assistant: boolean;
  title: string | null;
  summary: string | null;
  content_markdown: string;
  content_bundle: {
    assets: Array<{
      path: string;
      source_url: string | null;
      kind: "main" | "compiled" | "rule" | "doc";
      title: string | null;
      summary: string | null;
      rule_id: string | null;
      impact: string | null;
      tags: string[];
      content_markdown: string;
      content_sha256: string | null;
      frontmatter: Record<string, unknown>;
    }>;
  };
  content_sha256: string | null;
  version_digest?: string | null;
  last_validated_at: string | null;
  created_at: string;
  updated_at: string;
};

export type LocalUserItem = {
  id: string;
  workspace_id: string;
  login: string;
  role: string;
  is_active: boolean;
  auth_provider: string;
  auth_subject: string;
  display_name: string | null;
  api_rate_limit_per_hour: number | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceAccessItem = {
  workspace: {
    id: string;
    name: string;
    is_active: boolean;
    metadata: Record<string, string>;
    created_at: string;
    updated_at: string;
  };
  membership: WorkspaceMembershipItem;
  is_default?: boolean;
};

export type WorkspaceMembershipItem = {
  workspace_id: string;
  user_id: string;
  user_login?: string | null;
  user_display_name?: string | null;
  role: "owner" | "analyst" | "viewer";
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type WorkspaceResourceGrantItem = {
  id: string;
  resource_kind: WorkspaceResourceKind;
  resource_id: string;
  owner_workspace_id: string;
  recipient_workspace_id: string;
  alias: string;
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type WorkspaceResourceKind =
  | "database_connector"
  | "gitlab_connector"
  | "llm_connector"
  | "rag_connector"
  | "documentation_connector"
  | "mcp_connector"
  | "airflow_connector"
  | "kubernetes_connector"
  | "victoriametrics_connector"
  | "mattermost_connector"
  | "workspace_skill";

export type WorkspaceResourceOption = {
  id: string;
  name: string;
  kind: WorkspaceResourceKind;
};

export type WorkspaceUserItem = {
  id: string;
  home_workspace_id: string;
  login: string;
  display_name: string | null;
  system_role: string;
  is_active: boolean;
};

export type ConnectorType =
  | "database"
  | "llm"
  | "documentation"
  | "gitlab"
  | "mcp"
  | "airflow"
  | "kubernetes"
  | "victoriametrics"
  | "mattermost";

export type AnyConnectorItem =
  | DatabaseConnectorItem
  | LlmConnectorItem
  | DocumentationConnectorItem
  | GitLabConnectorItem
  | McpConnectorItem
  | AirflowConnectorItem
  | KubernetesConnectorItem
  | VictoriaMetricsConnectorItem
  | MattermostConnectorItem;

export type AdminSectionId = "overview" | "setup" | "governance" | "access" | "review";

export type AdminPageData = {
  isSystemAdmin?: boolean;
  databaseConnectors: DatabaseConnectorItem[];
  llmConnectors: LlmConnectorItem[];
  documentationConnectors: DocumentationConnectorItem[];
  gitlabConnectors: GitLabConnectorItem[];
  mcpConnectors: McpConnectorItem[];
  airflowConnectors: AirflowConnectorItem[];
  kubernetesConnectors: KubernetesConnectorItem[];
  victoriametricsConnectors: VictoriaMetricsConnectorItem[];
  mattermostConnectors: MattermostConnectorItem[];
  pendingInsights: RagInsightReviewItem[];
  scenarios: ScenarioDefinition[];
  workspaceSkills: WorkspaceSkillItem[];
  agents?: AgentProfile[];
  codingEnvironments?: CodingEnvironmentProfile[];
  codingTeams?: CodingTeamDetail[];
  localUsers: LocalUserItem[];
  currentUserId: string;
  workspaceId: string;
  workspaces: WorkspaceAccessItem[];
  workspaceMemberships: WorkspaceMembershipItem[];
  workspaceResourceGrants: WorkspaceResourceGrantItem[];
  workspaceUsers: WorkspaceUserItem[];
  workspaceResourceOptions: WorkspaceResourceOption[];
};
