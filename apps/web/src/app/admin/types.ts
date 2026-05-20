import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";

export type DatabaseConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_password: boolean;
  connection: {
    engine: "clickhouse" | "trino" | "greenplum" | "impala";
    host: string;
    port: number;
    username: string;
    database: string | null;
    catalog: string | null;
    schema: string | null;
    secure: boolean;
    verify: boolean;
    extra_settings: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type LlmConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_api_key: boolean;
  connection: {
    base_url: string;
    model: string;
    endpoint_path: string;
    verify_tls: boolean;
    temperature: number | null;
    max_tokens: number | null;
    headers: Record<string, string>;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type OpenMetadataConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_api_token: boolean;
  connection: {
    provider: "openmetadata";
    base_url: string;
    verify_tls: boolean;
    mcp_tables_url: string | null;
    service_name: string | null;
    database_name: string | null;
    schema_name: string | null;
    table_limit: number;
    fields: string;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type ConfluenceConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_api_token: boolean;
  connection: {
    provider: "confluence";
    base_url: string;
    username: string | null;
    auth_type: "bearer" | "basic";
    verify_tls: boolean;
    space_key: string | null;
    page_limit: number;
    extra_settings?: Record<string, string | number | boolean>;
  };
  created_at: string;
  updated_at: string;
};

export type JiraConnectorItem = {
  id: string;
  name: string;
  workspace_id: string;
  is_active: boolean;
  has_api_token: boolean;
  connection: {
    provider: "jira";
    base_url: string;
    username: string | null;
    auth_type: "bearer" | "basic";
    verify_tls: boolean;
    project_keys: string[];
    issue_limit: number;
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
  has_bot_token: boolean;
  connection: {
    provider: "mattermost";
    base_url: string;
    bot_username: string | null;
    verify_tls: boolean;
    listen_enabled: boolean;
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
  capability_family: "sql_optimization_advisor";
  engine_scope: "clickhouse" | "trino" | "greenplum" | "impala" | "any";
  priority: number;
  source_kind: "github_markdown_url";
  source_url: string;
  status: "active" | "inactive";
  validation_status: "valid" | "invalid" | "pending";
  title: string | null;
  summary: string | null;
  content_markdown: string;
  content_bundle: {
    assets: Array<{
      path: string;
      source_url: string;
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
  created_at: string;
  updated_at: string;
};

export type ConnectorType = "database" | "llm" | "metadata" | "confluence" | "jira" | "mattermost";

export type AnyConnectorItem =
  | DatabaseConnectorItem
  | LlmConnectorItem
  | OpenMetadataConnectorItem
  | ConfluenceConnectorItem
  | JiraConnectorItem
  | MattermostConnectorItem;

export type AdminSectionId = "overview" | "setup" | "governance" | "access" | "review";

export type AdminPageData = {
  databaseConnectors: DatabaseConnectorItem[];
  llmConnectors: LlmConnectorItem[];
  openMetadataConnectors: OpenMetadataConnectorItem[];
  confluenceConnectors: ConfluenceConnectorItem[];
  jiraConnectors: JiraConnectorItem[];
  mattermostConnectors: MattermostConnectorItem[];
  pendingInsights: RagInsightReviewItem[];
  scenarios: ScenarioDefinition[];
  workspaceSkills: WorkspaceSkillItem[];
  localUsers: LocalUserItem[];
  currentUserId: string;
  workspaceId: string;
};
