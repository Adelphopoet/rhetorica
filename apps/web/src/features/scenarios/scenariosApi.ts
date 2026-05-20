export type ScenarioToolPolicy = {
  allow_run_sql: boolean;
  sql_guard_required: boolean;
  allowed_tools: string[];
  post_answer_checklist: string[];
};

export type ScenarioConnectorBindings = {
  llm_connector_name: string | null;
  metadata_connector_name: string | null;
  database_connector_name: string | null;
  run_sql: boolean | null;
};

export type ScenarioDefinition = {
  id: string;
  slug: string;
  workspace_id: string;
  owner_user_id: string | null;
  scope: "official" | "personal";
  title: string;
  summary: string;
  description: string;
  input_schema: {
    type?: string;
    properties?: Record<string, Record<string, unknown>>;
    required?: string[];
  };
  prompt_template: string;
  tool_policy: ScenarioToolPolicy;
  default_connector_bindings: ScenarioConnectorBindings;
  status: "draft" | "published" | "archived";
  version: number;
  published_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ScenarioRunResponse = {
  scenario_run_id: string;
  scenario_definition_id: string;
  rendered_prompt: string;
  conversation_id: string | null;
};

export type SaveScenarioPayload = {
  workspace_id: string;
  scope: "official" | "personal";
  title: string;
  summary: string;
  description: string;
  prompt_template: string;
  input_schema: Record<string, unknown>;
  tool_policy: {
    allow_run_sql: boolean;
    sql_guard_required: true;
    allowed_tools: string[];
    post_answer_checklist: string[];
  };
  default_connector_bindings: {
    run_sql: boolean;
  };
  status: "draft" | "published";
};

function parseDetail(detail: unknown, fallbackStatus: number): never {
  if (typeof detail === "string" && detail.length > 0) {
    throw new Error(detail);
  }
  throw new Error(`Request failed with status ${fallbackStatus}`);
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  if (!response.ok) {
    parseDetail(payload.detail ?? payload.message ?? payload.body, response.status);
  }
  return payload as T;
}

export function scenarioListQueryKey(workspaceId: string) {
  return ["scenarios", workspaceId] as const;
}

export async function listScenarios(workspaceId: string): Promise<ScenarioDefinition[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  const response = await fetch(`/api/scenarios?${params.toString()}`, {
    cache: "no-store",
  });
  return parseJsonResponse<ScenarioDefinition[]>(response);
}

export async function createScenario(payload: SaveScenarioPayload): Promise<ScenarioDefinition> {
  const response = await fetch("/api/scenarios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<ScenarioDefinition>(response);
}

export async function updateScenario(
  scenarioId: string,
  payload: Omit<SaveScenarioPayload, "scope">
): Promise<ScenarioDefinition> {
  const response = await fetch(`/api/scenarios/${scenarioId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<ScenarioDefinition>(response);
}

export async function archiveScenario(
  scenarioId: string,
  workspaceId: string
): Promise<ScenarioDefinition> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  const response = await fetch(`/api/scenarios/${scenarioId}?${params.toString()}`, {
    method: "DELETE",
  });
  return parseJsonResponse<ScenarioDefinition>(response);
}

export async function runScenario(
  scenarioId: string,
  workspaceId: string,
  inputs: Record<string, unknown>,
  conversationId?: string | null
): Promise<ScenarioRunResponse> {
  const response = await fetch(`/api/scenarios/${scenarioId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: workspaceId,
      conversation_id: conversationId ?? null,
      inputs,
    }),
  });
  return parseJsonResponse<ScenarioRunResponse>(response);
}
