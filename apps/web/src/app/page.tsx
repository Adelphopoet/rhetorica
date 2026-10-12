import { ChatWorkspace } from "./components/chat-workspace";
import type { AgentProfile } from "@/features/agents/agentsApi";
import type { ConversationSummary } from "@/features/chat/historyApi";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { fetchBackend } from "@/lib/backendAuth";
import { requireServerSession } from "@/lib/serverSession";
import { cookies } from "next/headers";
import { UI_COMPOSER_DOCK_COOKIE, UI_RESULT_PANEL_COOKIE, UI_SIDEBAR_COLLAPSED_COOKIE, UI_COMPOSER_DOCK_WIDTH_COOKIE, UI_COMPOSER_DOCK_HEIGHT_COOKIE, UI_SIDEBAR_WIDTH_COOKIE } from "@/lib/uiCookies";

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

type WorkspaceAccessItem = {
  workspace: { id: string; name: string };
  membership: { role: "owner" | "analyst" | "viewer" };
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
  initialAgents: AgentProfile[];
  workspaces: Array<{ id: string; name: string; role: "owner" | "analyst" | "viewer" }>;
}> {
  const session = await requireServerSession("/");
  const workspaceName = session.workspace_id;
  const [health, executions, conversations, scenarios, agents, workspaces] = await Promise.all([
    fetchApi<HealthcheckResponse>("/healthz"),
    fetchApi<ExecutionHistoryItem[]>(`/v1/sql/executions?workspace_id=${workspaceName}&limit=8`),
    fetchApi<ConversationSummary[]>(`/v1/chat/conversations?workspace_id=${workspaceName}&limit=25`),
    fetchApi<ScenarioDefinition[]>(`/v1/scenarios?workspace_id=${workspaceName}`),
    fetchApi<AgentProfile[]>(`/v1/agents?workspace_id=${workspaceName}`),
    fetchApi<WorkspaceAccessItem[]>("/v1/workspaces"),
  ]);

  return {
    workspaceName,
    canAccessAdmin: session.role === "admin" || session.workspace_role === "owner",
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
    initialAgents: agents ?? [],
    workspaces: (workspaces ?? []).map(({ workspace, membership }) => ({
      id: workspace.id,
      name: workspace.name,
      role: membership.role,
    })),
  };
}

export default async function HomePage() {
  const backend = await loadBackendState();
  const cookieStore = await cookies();
  const composerDockCookie = cookieStore.get(UI_COMPOSER_DOCK_COOKIE)?.value;
  const resultPanelCookie = cookieStore.get(UI_RESULT_PANEL_COOKIE)?.value;
  const sidebarCollapsedCookie = cookieStore.get(UI_SIDEBAR_COLLAPSED_COOKIE)?.value;
  const composerDockWidthCookie = cookieStore.get(UI_COMPOSER_DOCK_WIDTH_COOKIE)?.value;
  const composerDockHeightCookie = cookieStore.get(UI_COMPOSER_DOCK_HEIGHT_COOKIE)?.value;
  const sidebarWidthCookie = cookieStore.get(UI_SIDEBAR_WIDTH_COOKIE)?.value;
  const composerDockWidth = Number.parseInt(composerDockWidthCookie ?? "", 10);
  const composerDockHeight = Number.parseInt(composerDockHeightCookie ?? "", 10);
  const sidebarWidth = Number.parseInt(sidebarWidthCookie ?? "", 10);
  return (
    <ChatWorkspace
      workspaceName={backend.workspaceName}
      canAccessAdmin={backend.canAccessAdmin}
      apiStatus={backend.apiStatus}
      currentUser={backend.currentUser}
      recentExecutions={backend.recentExecutions}
      initialConversations={backend.initialConversations}
      initialScenarios={backend.initialScenarios}
      initialAgents={backend.initialAgents}
      workspaces={backend.workspaces}
      initialComposerDock={composerDockCookie === "right" ? "right" : "bottom"}
      initialResultPanelOpen={resultPanelCookie === "1"}
      initialSidebarCollapsed={sidebarCollapsedCookie === "1"}
      initialComposerDockWidth={Number.isFinite(composerDockWidth) ? composerDockWidth : undefined}
      initialComposerDockHeight={Number.isFinite(composerDockHeight) ? composerDockHeight : undefined}
      initialSidebarWidth={Number.isFinite(sidebarWidth) ? sidebarWidth : undefined}
    />
  );
}
