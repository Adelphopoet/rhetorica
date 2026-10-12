import type { Metadata } from "next";
import { AdminPageClient } from "./AdminPageClient";
import type { AgentProfile } from "@/features/agents/agentsApi";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { fetchBackend } from "@/lib/backendAuth";
import { requireAdminSession } from "@/lib/serverSession";
import type {
  AirflowConnectorItem,
  DatabaseConnectorItem,
  DocumentationConnectorItem,
  GitLabConnectorItem,
  KubernetesConnectorItem,
  LlmConnectorItem,
  LocalUserItem,
  MattermostConnectorItem,
  McpConnectorItem,
  RagInsightReviewItem,
  VictoriaMetricsConnectorItem,
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

async function fetchPendingRagInsights(workspaceId: string): Promise<RagInsightReviewItem[]> {
  return fetchApi<RagInsightReviewItem>(
    `/v1/admin/rag-insights/pending?workspace_id=${workspaceId}&limit=100`,
  );
}

async function fetchDocumentationConnectors(workspaceId: string): Promise<DocumentationConnectorItem[]> {
  return fetchApi<DocumentationConnectorItem>(
    `/v1/admin/documentation-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchGitLabConnectors(workspaceId: string): Promise<GitLabConnectorItem[]> {
  return fetchApi<GitLabConnectorItem>(
    `/v1/admin/gitlab-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchMcpConnectors(workspaceId: string): Promise<McpConnectorItem[]> {
  return fetchApi<McpConnectorItem>(
    `/v1/admin/mcp-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchAirflowConnectors(workspaceId: string): Promise<AirflowConnectorItem[]> {
  return fetchApi<AirflowConnectorItem>(
    `/v1/admin/airflow-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchKubernetesConnectors(workspaceId: string): Promise<KubernetesConnectorItem[]> {
  return fetchApi<KubernetesConnectorItem>(
    `/v1/admin/kubernetes-connectors?workspace_id=${workspaceId}&limit=200`,
  );
}

async function fetchVictoriaMetricsConnectors(
  workspaceId: string,
): Promise<VictoriaMetricsConnectorItem[]> {
  return fetchApi<VictoriaMetricsConnectorItem>(
    `/v1/admin/victoriametrics-connectors?workspace_id=${workspaceId}&limit=200`,
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
    `/v1/admin/skills?workspace_id=${workspaceId}&limit=500`,
  );
}

async function fetchAgents(workspaceId: string): Promise<AgentProfile[]> {
  return fetchApi<AgentProfile>(`/v1/admin/agents?workspace_id=${workspaceId}&limit=200`);
}

async function fetchLocalUsers(): Promise<LocalUserItem[]> {
  return fetchApi<LocalUserItem>("/v1/admin/users");
}

export const metadata: Metadata = {
  title: "Администрирование | Rhetorica",
};

export default async function AdminPage() {
  const session = await requireAdminSession("/admin");
  const workspaceId = session.workspace_id;
  const [databaseConnectors, llmConnectors, documentationConnectors, gitlabConnectors, mcpConnectors, airflowConnectors, kubernetesConnectors, victoriametricsConnectors, mattermostConnectors, pendingInsights, scenarios, workspaceSkills, agents, localUsers] = await Promise.all([
    fetchDatabaseConnectors(workspaceId),
    fetchLlmConnectors(workspaceId),
    fetchDocumentationConnectors(workspaceId),
    fetchGitLabConnectors(workspaceId),
    fetchMcpConnectors(workspaceId),
    fetchAirflowConnectors(workspaceId),
    fetchKubernetesConnectors(workspaceId),
    fetchVictoriaMetricsConnectors(workspaceId),
    fetchMattermostConnectors(workspaceId),
    fetchPendingRagInsights(workspaceId),
    fetchScenarios(workspaceId),
    fetchWorkspaceSkills(workspaceId),
    fetchAgents(workspaceId),
    fetchLocalUsers(),
  ]);

  return (
    <AdminPageClient
      databaseConnectors={databaseConnectors}
      llmConnectors={llmConnectors}
      documentationConnectors={documentationConnectors}
      gitlabConnectors={gitlabConnectors}
      mcpConnectors={mcpConnectors}
      airflowConnectors={airflowConnectors}
      kubernetesConnectors={kubernetesConnectors}
      victoriametricsConnectors={victoriametricsConnectors}
      mattermostConnectors={mattermostConnectors}
      pendingInsights={pendingInsights}
      scenarios={scenarios}
      workspaceSkills={workspaceSkills}
      agents={agents}
      localUsers={localUsers}
      currentUserId={session.user.id}
      workspaceId={workspaceId}
    />
  );
}
