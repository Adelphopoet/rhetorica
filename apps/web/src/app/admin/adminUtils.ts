import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";

import type {
  AnyConnectorItem,
  ConfluenceConnectorItem,
  DatabaseConnectorItem,
  JiraConnectorItem,
  LlmConnectorItem,
  MattermostConnectorItem,
  OpenMetadataConnectorItem,
  RagInsightReviewItem,
  WorkspaceSkillItem,
  LocalUserItem,
  ConnectorType,
} from "./types";

export type SetupStatusFilter = "all" | "ready" | "attention";

export function formatShortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

export function formatDateTime(value: string | null) {
  if (!value) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function getConnectorTypeLabel(type: ConnectorType) {
  switch (type) {
    case "database":
      return "Database";
    case "llm":
      return "LLM";
    case "metadata":
      return "OpenMetadata";
    case "confluence":
      return "Confluence";
    case "jira":
      return "Jira";
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
  if (type === "metadata") {
    const item = connector as OpenMetadataConnectorItem;
    return item.has_api_token && item.connection.verify_tls ? "ready" : "attention";
  }
  if (type === "confluence") {
    const item = connector as ConfluenceConnectorItem;
    return item.has_api_token && item.connection.verify_tls ? "ready" : "attention";
  }
  if (type === "jira") {
    const item = connector as JiraConnectorItem;
    return item.has_api_token && item.connection.verify_tls ? "ready" : "attention";
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
  if (type === "metadata") {
    const item = connector as OpenMetadataConnectorItem;
    base.push(item.connection.base_url, item.connection.service_name ?? "");
  }
  if (type === "confluence") {
    const item = connector as ConfluenceConnectorItem;
    base.push(item.connection.base_url, item.connection.space_key ?? "");
  }
  if (type === "jira") {
    const item = connector as JiraConnectorItem;
    base.push(item.connection.base_url, item.connection.project_keys.join(" "));
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
  openMetadataConnectors: OpenMetadataConnectorItem[];
  confluenceConnectors: ConfluenceConnectorItem[];
  jiraConnectors: JiraConnectorItem[];
  mattermostConnectors: MattermostConnectorItem[];
  pendingInsights: RagInsightReviewItem[];
  scenarios: ScenarioDefinition[];
  workspaceSkills: WorkspaceSkillItem[];
  localUsers: LocalUserItem[];
}) {
  const blockers: string[] = [];
  const activeDatabaseConnectors = args.databaseConnectors.filter((item) => item.is_active);
  const activeLlmConnectors = args.llmConnectors.filter((item) => item.is_active);
  const activeOpenMetadataConnectors = args.openMetadataConnectors.filter((item) => item.is_active);

  if (activeDatabaseConnectors.length === 0) {
    blockers.push("No active database connector configured");
  }
  if (activeLlmConnectors.length === 0) {
    blockers.push("No active LLM connector configured");
  }
  if (activeOpenMetadataConnectors.length === 0) {
    blockers.push("No active OpenMetadata connector configured");
  }
  if (args.localUsers.length <= 1) {
    blockers.push("Only one local user exists");
  }
  if (args.pendingInsights.length > 0) {
    blockers.push(`${args.pendingInsights.length} insight review items pending`);
  }

  const checks = [
    activeDatabaseConnectors.length > 0,
    activeLlmConnectors.length > 0,
    activeOpenMetadataConnectors.length > 0,
    args.scenarios.some((scenario) => scenario.scope === "official" && scenario.status === "published"),
    args.workspaceSkills.some((skill) => skill.status === "active"),
    args.localUsers.length > 1,
  ];

  const completedChecks = checks.filter(Boolean).length;
  const readinessScore = Math.round((completedChecks / checks.length) * 100);

  const totalSecureConnections =
    args.databaseConnectors.filter((item) => item.connection.secure).length +
    args.llmConnectors.filter((item) => item.connection.verify_tls).length +
    args.openMetadataConnectors.filter((item) => item.connection.verify_tls).length +
    args.confluenceConnectors.filter((item) => item.connection.verify_tls).length +
    args.jiraConnectors.filter((item) => item.connection.verify_tls).length +
    args.mattermostConnectors.filter((item) => item.connection.verify_tls).length;

  const totalConnections =
    args.databaseConnectors.length +
    args.llmConnectors.length +
    args.openMetadataConnectors.length +
    args.confluenceConnectors.length +
    args.jiraConnectors.length +
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
