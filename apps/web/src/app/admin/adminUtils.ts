import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { formatInMoscow } from "@/lib/dateTime";

import type {
  AnyConnectorItem,
  AirflowConnectorItem,
  DatabaseConnectorItem,
  DocumentationConnectorItem,
  GitLabConnectorItem,
  KubernetesConnectorItem,
  LlmConnectorItem,
  MattermostConnectorItem,
  McpConnectorItem,
  RagInsightReviewItem,
  VictoriaMetricsConnectorItem,
  WorkspaceSkillItem,
  LocalUserItem,
  ConnectorType,
} from "./types";

export type SetupStatusFilter = "all" | "ready" | "attention";

export function formatShortDate(value: string) {
  return formatInMoscow(value, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }) ?? value;
}

export function formatDateTime(value: string | null) {
  if (!value) {
    return "—";
  }

  return formatInMoscow(value, {
    dateStyle: "medium",
    timeStyle: "short",
  }) ?? value;
}

export function getConnectorTypeLabel(type: ConnectorType) {
  switch (type) {
    case "database":
      return "База данных";
    case "llm":
      return "LLM";
    case "documentation":
      return "Документация";
    case "gitlab":
      return "GitLab";
    case "mcp":
      return "MCP";
    case "airflow":
      return "Airflow";
    case "kubernetes":
      return "Kubernetes";
    case "victoriametrics":
      return "VictoriaMetrics";
    case "mattermost":
      return "Tag";
  }
}

export function getConnectorHealth(
  connector: AnyConnectorItem,
  type: ConnectorType
): "ready" | "attention" {
  if (!connector.is_active) {
    return "attention";
  }

  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    return item.has_password && item.connection.secure ? "ready" : "attention";
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    return item.has_api_key && item.connection.verify_tls ? "ready" : "attention";
  }
  if (type === "documentation") {
    const item = connector as DocumentationConnectorItem;
    return item.connection.verify_tls && item.connection.sync_enabled ? "ready" : "attention";
  }
  if (type === "gitlab") {
    const item = connector as GitLabConnectorItem;
    return item.has_api_token && item.connection.verify_tls ? "ready" : "attention";
  }
  if (type === "mcp") {
    const item = connector as McpConnectorItem;
    return item.has_secret_headers && item.connection.verify_tls && item.tool_count > 0
      ? "ready"
      : "attention";
  }
  if (type === "airflow") {
    const item = connector as AirflowConnectorItem;
    return item.has_password && item.connection.verify_tls ? "ready" : "attention";
  }
  if (type === "kubernetes") {
    const item = connector as KubernetesConnectorItem;
    return item.has_bearer_token && item.connection.verify_tls && item.connection.namespace_allowlist.length > 0
      ? "ready"
      : "attention";
  }
  if (type === "victoriametrics") {
    const item = connector as VictoriaMetricsConnectorItem;
    const tokenReady = item.connection.auth_type === "none" || item.has_bearer_token;
    return tokenReady && item.connection.verify_tls && item.connection.templates.length > 0 ? "ready" : "attention";
  }
  const item = connector as MattermostConnectorItem;
  return item.has_bot_token && item.connection.verify_tls && item.connection.listen_enabled ? "ready" : "attention";
}

export function getConnectorSearchText(
  connector: AnyConnectorItem,
  type: ConnectorType
) {
  const base = [connector.name, connector.workspace_id];

  if (type === "database") {
    const item = connector as DatabaseConnectorItem;
    base.push(
      item.connection.engine,
      item.connection.host,
      item.connection.database ?? "",
      item.connection.catalog ?? "",
      item.connection.schema ?? ""
    );
  }
  if (type === "llm") {
    const item = connector as LlmConnectorItem;
    base.push(item.connection.model, item.connection.base_url);
  }
  if (type === "documentation") {
    const item = connector as DocumentationConnectorItem;
    base.push(
      item.connection.base_url,
      item.connection.index_url ?? "",
      item.connection.embedding_model,
      item.connection.qdrant_collection_name
    );
  }
  if (type === "gitlab") {
    const item = connector as GitLabConnectorItem;
    base.push(
      item.connection.base_url,
      item.connection.use_when,
      item.connection.default_project_paths.join(" "),
      item.connection.default_group_paths.join(" ")
    );
  }
  if (type === "mcp") {
    const item = connector as McpConnectorItem;
    const allowedToolNames = item.connection.allowed_tool_names ?? [];
    base.push(
      item.connection.provider,
      item.connection.url,
      item.connection.enabled_groups ?? "",
      allowedToolNames.join(" "),
      item.connection.tool_name_prefix ?? "",
      item.connection.usage_description ?? "",
      item.secret_header_names.join(" ")
    );
  }
  if (type === "airflow") {
    const item = connector as AirflowConnectorItem;
    base.push(
      item.connection.base_url,
      item.connection.api_path,
      item.connection.username,
      item.connection.use_when
    );
  }
  if (type === "kubernetes") {
    const item = connector as KubernetesConnectorItem;
    base.push(
      item.connection.base_url,
      item.connection.use_when,
      item.connection.namespace_allowlist.join(" ")
    );
  }
  if (type === "victoriametrics") {
    const item = connector as VictoriaMetricsConnectorItem;
    base.push(
      item.connection.base_url,
      item.connection.use_when,
      item.connection.templates.map((template) => `${template.template_id} ${template.title}`).join(" ")
    );
  }
  if (type === "mattermost") {
    const item = connector as MattermostConnectorItem;
    base.push(item.connection.base_url, item.connection.bot_username ?? "");
  }

  return base.join(" ").toLowerCase();
}

export function matchesConnectorFilter(
  connector: AnyConnectorItem,
  type: ConnectorType,
  search: string,
  status: SetupStatusFilter
) {
  const normalizedSearch = search.trim().toLowerCase();
  if (normalizedSearch && !getConnectorSearchText(connector, type).includes(normalizedSearch)) {
    return false;
  }

  if (status === "all") {
    return true;
  }

  return getConnectorHealth(connector, type) === status;
}

export function sortScenariosForGovernance(scenarios: ScenarioDefinition[]) {
  return [...scenarios]
    .filter((scenario) => scenario.scope === "official")
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function sortSkills(skills: WorkspaceSkillItem[]) {
  return [...skills].sort((left, right) => {
    if (right.priority !== left.priority) {
      return right.priority - left.priority;
    }
    return right.updated_at.localeCompare(left.updated_at);
  });
}

export function sortUsers(users: LocalUserItem[]) {
  return [...users].sort((left, right) => left.created_at.localeCompare(right.created_at));
}

export function buildReadinessModel(args: {
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
  localUsers: LocalUserItem[];
}) {
  const blockers: string[] = [];
  const activeDatabaseConnectors = args.databaseConnectors.filter((item) => item.is_active);
  const activeLlmConnectors = args.llmConnectors.filter((item) => item.is_active);

  if (activeDatabaseConnectors.length === 0) {
    blockers.push("Нет активного подключения к базе данных");
  }
  if (activeLlmConnectors.length === 0) {
    blockers.push("Нет активного подключения LLM");
  }
  if (args.localUsers.length <= 1) {
    blockers.push("Существует только один локальный пользователь");
  }
  if (args.pendingInsights.length > 0) {
    blockers.push(`${args.pendingInsights.length} инсайтов ожидают проверки`);
  }

  const checks = [
    activeDatabaseConnectors.length > 0,
    activeLlmConnectors.length > 0,
    args.scenarios.some((scenario) => scenario.scope === "official" && scenario.status === "published"),
    args.workspaceSkills.some((skill) => skill.status === "active"),
    args.localUsers.length > 1,
  ];

  const completedChecks = checks.filter(Boolean).length;
  const readinessScore = Math.round((completedChecks / checks.length) * 100);

  const totalSecureConnections =
    args.databaseConnectors.filter((item) => item.connection.secure).length +
    args.llmConnectors.filter((item) => item.connection.verify_tls).length +
    args.documentationConnectors.filter((item) => item.connection.verify_tls).length +
    args.gitlabConnectors.filter((item) => item.connection.verify_tls).length +
    args.mcpConnectors.filter((item) => item.connection.verify_tls).length +
    args.airflowConnectors.filter((item) => item.connection.verify_tls).length +
    args.kubernetesConnectors.filter((item) => item.connection.verify_tls).length +
    args.victoriametricsConnectors.filter((item) => item.connection.verify_tls).length +
    args.mattermostConnectors.filter((item) => item.connection.verify_tls).length;

  const totalConnections =
    args.databaseConnectors.length +
    args.llmConnectors.length +
    args.documentationConnectors.length +
    args.gitlabConnectors.length +
    args.mcpConnectors.length +
    args.airflowConnectors.length +
    args.kubernetesConnectors.length +
    args.victoriametricsConnectors.length +
    args.mattermostConnectors.length;

  return {
    blockers,
    readinessScore,
    completedChecks,
    totalChecks: checks.length,
    totalSecureConnections,
    totalConnections,
    publishedScenarioCount: args.scenarios.filter(
      (scenario) => scenario.scope === "official" && scenario.status === "published"
    ).length,
    activeSkillCount: args.workspaceSkills.filter((skill) => skill.status === "active").length,
  };
}
