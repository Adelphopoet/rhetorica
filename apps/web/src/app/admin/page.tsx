import type { Metadata } from "next";
import { AdminPageClient } from "./AdminPageClient";
import type { AgentProfile } from "@/features/agents/agentsApi";
import type { CodingTeamDetail } from "@/features/coding/codingApi";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { fetchBackend } from "@/lib/backendAuth";
import { requireWorkspaceManagerSession } from "@/lib/serverSession";
import type {
  AirflowConnectorItem,
  CodingEnvironmentProfile,
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
  WorkspaceAccessItem,
  WorkspaceMembershipItem,
  WorkspaceResourceGrantItem,
  WorkspaceResourceOption,
  WorkspaceSkillItem,
  WorkspaceUserItem,
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

async function fetchCodingEnvironments(): Promise<CodingEnvironmentProfile[]> {
  return fetchApi<CodingEnvironmentProfile>("/v1/admin/coding-environments");
}

async function fetchCodingTeams(): Promise<CodingTeamDetail[]> {
  return fetchApi<CodingTeamDetail>("/v1/coding-agent-teams");
}

async function fetchLocalUsers(): Promise<LocalUserItem[]> {
  return fetchApi<LocalUserItem>("/v1/admin/users");
}

async function fetchWorkspaces(): Promise<WorkspaceAccessItem[]> {
  return fetchApi<WorkspaceAccessItem>("/v1/workspaces");
}

async function fetchWorkspaceMemberships(workspaceId: string): Promise<WorkspaceMembershipItem[]> {
  return fetchApi<WorkspaceMembershipItem>(
    `/v1/admin/workspaces/${encodeURIComponent(workspaceId)}/memberships`,
  );
}

async function fetchWorkspaceResourceGrants(
  workspaceId: string,
): Promise<WorkspaceResourceGrantItem[]> {
  return fetchApi<WorkspaceResourceGrantItem>(
    `/v1/admin/workspaces/${encodeURIComponent(workspaceId)}/resource-grants`,
  );
}

async function fetchWorkspaceUsers(): Promise<WorkspaceUserItem[]> {
  return fetchApi<WorkspaceUserItem>("/v1/admin/workspaces/users");
}

async function fetchWorkspaceResourceOptions(
  workspaceId: string,
): Promise<WorkspaceResourceOption[]> {
  return fetchApi<WorkspaceResourceOption>(
    `/v1/admin/workspaces/${encodeURIComponent(workspaceId)}/local-resources`,
  );
}

export const metadata: Metadata = {
  title: "Администрирование | Rhetorica",
};

export default async function AdminPage() {
  const session = await requireWorkspaceManagerSession("/admin");
  const isSystemAdmin = session.role === "admin";
  const workspaceId = session.workspace_id;
  const [
    databaseConnectors,
    llmConnectors,
    documentationConnectors,
    gitlabConnectors,
    mcpConnectors,
    airflowConnectors,
    kubernetesConnectors,
    victoriametricsConnectors,
    mattermostConnectors,
    pendingInsights,
    scenarios,
    workspaceSkills,
    agents,
    codingEnvironments,
    codingTeams,
    localUsers,
    workspaces,
    workspaceMemberships,
    workspaceResourceGrants,
    workspaceUsers,
    workspaceResourceOptions,
  ] = await Promise.all([
    fetchDatabaseConnectors(workspaceId),
    fetchLlmConnectors(workspaceId),
    fetchDocumentationConnectors(workspaceId),
    fetchGitLabConnectors(workspaceId),
    fetchMcpConnectors(workspaceId),
    fetchAirflowConnectors(workspaceId),
    fetchKubernetesConnectors(workspaceId),
    fetchVictoriaMetricsConnectors(workspaceId),
    fetchMattermostConnectors(workspaceId),
    isSystemAdmin ? fetchPendingRagInsights(workspaceId) : Promise.resolve([]),
    isSystemAdmin ? fetchScenarios(workspaceId) : Promise.resolve([]),
    fetchWorkspaceSkills(workspaceId),
    isSystemAdmin ? fetchAgents(workspaceId) : Promise.resolve([]),
    isSystemAdmin ? fetchCodingEnvironments() : Promise.resolve([]),
    isSystemAdmin ? fetchCodingTeams() : Promise.resolve([]),
    isSystemAdmin ? fetchLocalUsers() : Promise.resolve([]),
    isSystemAdmin ? fetchWorkspaces() : Promise.resolve([]),
    isSystemAdmin ? fetchWorkspaceMemberships(workspaceId) : Promise.resolve([]),
    isSystemAdmin ? fetchWorkspaceResourceGrants(workspaceId) : Promise.resolve([]),
    isSystemAdmin ? fetchWorkspaceUsers() : Promise.resolve([]),
    isSystemAdmin ? fetchWorkspaceResourceOptions(workspaceId) : Promise.resolve([]),
  ]);

  return (
    <AdminPageClient
      isSystemAdmin={isSystemAdmin}
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
      codingEnvironments={codingEnvironments}
      codingTeams={codingTeams}
      localUsers={localUsers}
      currentUserId={session.user.id}
      workspaceId={workspaceId}
      workspaces={workspaces}
      workspaceMemberships={workspaceMemberships}
      workspaceResourceGrants={workspaceResourceGrants}
      workspaceUsers={workspaceUsers}
      workspaceResourceOptions={workspaceResourceOptions}
    />
  );
}
