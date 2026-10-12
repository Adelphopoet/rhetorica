export type AgentSettings = {
  run_sql_allowed: boolean;
  temperature: number | null;
  max_tokens: number | null;
};

export type AgentProfile = {
  id: string;
  workspace_id: string;
  slug: string;
  title: string;
  description: string | null;
  admin_prompt?: string | null;
  is_active: boolean;
  llm_connector_name: string | null;
  allowed_database_connector_names: string[];
  allowed_rag_connector_names: string[];
  allowed_documentation_connector_names: string[];
  allowed_gitlab_connector_names: string[];
  allowed_mcp_connector_names: string[];
  allowed_airflow_connector_names: string[];
  allowed_kubernetes_connector_names: string[];
  allowed_victoriametrics_connector_names: string[];
  allowed_workspace_skill_ids: string[];
  settings: AgentSettings;
  star_count: number;
  is_starred: boolean;
  is_favorite: boolean;
  created_at: string;
  updated_at: string;
};

export function agentListQueryKey(workspaceId: string) {
  return ["agents", workspaceId] as const;
}

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, { ...init, cache: "no-store" });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text || `Запрос завершился с ошибкой ${response.status}`);
  }
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export async function listAgents(workspaceId: string): Promise<AgentProfile[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  return requestJson<AgentProfile[]>(`/api/agents?${params.toString()}`);
}

export async function setAgentStar(
  workspaceId: string,
  agentId: string,
  enabled: boolean,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  await requestJson(`/api/agents/${agentId}/star?${params.toString()}`, {
    method: enabled ? "POST" : "DELETE",
  });
}

export async function setAgentFavorite(
  workspaceId: string,
  agentId: string,
  enabled: boolean,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  await requestJson(`/api/agents/${agentId}/favorite?${params.toString()}`, {
    method: enabled ? "POST" : "DELETE",
  });
}
