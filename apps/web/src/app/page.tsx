import { ChatWorkspace } from "./components/chat-workspace";
import type { ConversationSummary } from "@/features/chat/historyApi";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { fetchBackend } from "@/lib/backendAuth";
import { requireServerSession } from "@/lib/serverSession";

type HealthcheckResponse = {
  status: string;
  env: string;
};

type ExecutionHistoryItem = {
  execution_id: string;
  status: string;
  submitted_sql: string;
  updated_at: string;
  duration_ms: number | null;
  row_count: number | null;
};

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await fetchBackend(path, {
      ...init,
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

async function loadBackendState(): Promise<{
  workspaceName: string;
  canAccessAdmin: boolean;
  apiStatus: string;
  currentUser: {
    login: string;
    displayName: string | null;
    role: string;
    authProvider: string;
  };
  recentExecutions: ExecutionHistoryItem[];
  initialConversations: ConversationSummary[];
  initialScenarios: ScenarioDefinition[];
}> {
  const session = await requireServerSession("/");
  const workspaceName = session.workspace_id;
  const [health, executions, conversations, scenarios] = await Promise.all([
    fetchApi<HealthcheckResponse>("/healthz"),
    fetchApi<ExecutionHistoryItem[]>(`/v1/sql/executions?workspace_id=${workspaceName}&limit=8`),
    fetchApi<ConversationSummary[]>(`/v1/chat/conversations?workspace_id=${workspaceName}&limit=24`),
    fetchApi<ScenarioDefinition[]>(`/v1/scenarios?workspace_id=${workspaceName}`),
  ]);

  return {
    workspaceName,
    canAccessAdmin: session.role === "admin",
    apiStatus: health ? `${health.status} (${health.env})` : "offline",
    currentUser: {
      login: session.user.login,
      displayName: session.user.display_name,
      role: session.role,
      authProvider: session.user.auth_provider,
    },
    recentExecutions: executions ?? [],
    initialConversations: conversations ?? [],
    initialScenarios: scenarios ?? [],
  };
}

export default async function HomePage() {
  const backend = await loadBackendState();
  return (
    <ChatWorkspace
      workspaceName={backend.workspaceName}
      canAccessAdmin={backend.canAccessAdmin}
      apiStatus={backend.apiStatus}
      currentUser={backend.currentUser}
      recentExecutions={backend.recentExecutions}
      initialConversations={backend.initialConversations}
      initialScenarios={backend.initialScenarios}
    />
  );
}
