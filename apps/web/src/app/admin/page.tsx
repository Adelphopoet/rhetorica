import { AdminPageClient } from "./AdminPageClient";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { fetchBackend } from "@/lib/backendAuth";
import { requireAdminSession } from "@/lib/serverSession";
import type {
  ConfluenceConnectorItem,
  DatabaseConnectorItem,
  JiraConnectorItem,
  LlmConnectorItem,
  LocalUserItem,
  MattermostConnectorItem,
  OpenMetadataConnectorItem,
  RagInsightReviewItem,
  WorkspaceSkillItem,
} from "./types";

async function fetchApi<T>(path: string): Promise<T[]> {
  try {
    const response = await fetchBackend(path, { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    return (await response.json()) as T[];
  } catch {
    return [];
  }
}

async function fetchDatabaseConnectors(workspaceId: string): Promise<DatabaseConnectorItem[]> {
  return fetchApi<DatabaseConnectorItem>(
    `/v1/admin/connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchLlmConnectors(workspaceId: string): Promise<LlmConnectorItem[]> {
  return fetchApi<LlmConnectorItem>(
    `/v1/admin/llm-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchOpenMetadataConnectors(
  workspaceId: string,
): Promise<OpenMetadataConnectorItem[]> {
  return fetchApi<OpenMetadataConnectorItem>(
    `/v1/admin/openmetadata-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchPendingRagInsights(workspaceId: string): Promise<RagInsightReviewItem[]> {
  return fetchApi<RagInsightReviewItem>(
    `/v1/admin/rag-insights/pending?workspace_id=${workspaceId}&limit=100`,
  );
}

async function fetchConfluenceConnectors(workspaceId: string): Promise<ConfluenceConnectorItem[]> {
  return fetchApi<ConfluenceConnectorItem>(
    `/v1/admin/confluence-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchJiraConnectors(workspaceId: string): Promise<JiraConnectorItem[]> {
  return fetchApi<JiraConnectorItem>(
    `/v1/admin/jira-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchMattermostConnectors(workspaceId: string): Promise<MattermostConnectorItem[]> {
  return fetchApi<MattermostConnectorItem>(
    `/v1/admin/mattermost-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchScenarios(workspaceId: string): Promise<ScenarioDefinition[]> {
  return fetchApi<ScenarioDefinition>(
    `/v1/scenarios?workspace_id=${workspaceId}`,
  );
}

async function fetchWorkspaceSkills(workspaceId: string): Promise<WorkspaceSkillItem[]> {
  return fetchApi<WorkspaceSkillItem>(
    `/v1/admin/skills?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchLocalUsers(): Promise<LocalUserItem[]> {
  return fetchApi<LocalUserItem>("/v1/admin/users");
}

export default async function AdminPage() {
  const session = await requireAdminSession("/admin");
  const workspaceId = session.workspace_id;
  const [databaseConnectors, llmConnectors, openMetadataConnectors, confluenceConnectors, jiraConnectors, mattermostConnectors, pendingInsights, scenarios, workspaceSkills, localUsers] = await Promise.all([
    fetchDatabaseConnectors(workspaceId),
    fetchLlmConnectors(workspaceId),
    fetchOpenMetadataConnectors(workspaceId),
    fetchConfluenceConnectors(workspaceId),
    fetchJiraConnectors(workspaceId),
    fetchMattermostConnectors(workspaceId),
    fetchPendingRagInsights(workspaceId),
    fetchScenarios(workspaceId),
    fetchWorkspaceSkills(workspaceId),
    fetchLocalUsers(),
  ]);

  return (
    <AdminPageClient
      databaseConnectors={databaseConnectors}
      llmConnectors={llmConnectors}
      openMetadataConnectors={openMetadataConnectors}
      confluenceConnectors={confluenceConnectors}
      jiraConnectors={jiraConnectors}
      mattermostConnectors={mattermostConnectors}
      pendingInsights={pendingInsights}
      scenarios={scenarios}
      workspaceSkills={workspaceSkills}
      localUsers={localUsers}
      currentUserId={session.user.id}
      workspaceId={workspaceId}
    />
  );
}
