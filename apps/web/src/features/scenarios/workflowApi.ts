export type ValueType = "string" | "number" | "boolean" | "json" | "table" | "file" | "files";
export type ValueSpec = { type: ValueType; title?: string; required?: boolean; default?: unknown; json_schema?: Record<string, unknown> | null };
export type Binding = { source: "constant" | "input" | "step"; type: ValueType; value?: unknown; node_id?: string | null; path?: (string | number)[]; required?: boolean; default?: unknown };
export type NodeType = "input" | "sql" | "python" | "llm" | "coding_task" | "condition" | "parallel" | "join" | "approval" | "result";
export type WorkflowNode = {
  id: string; title: string; type: NodeType; inputs: Record<string, Binding>; outputs: Record<string, ValueSpec>;
  connector_name?: string; sql?: string; row_limit?: number; timeout_ms?: number; code?: string;
  agent_id?: string; instructions?: string; output_format?: "text" | "json"; allowed_tools?: string[];
  dbt_project_subdir?: string;
  root_agent_id?: string; developer_agent_id?: string; qa_agent_id?: string; gitlab_connector_id?: string; project_id?: string; target_branch?: string; environment_profile_id?: string; database_connector_id?: string | null;
  rules?: { input: string; operator: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "contains" | "is_empty"; value?: unknown }[];
  match?: "all" | "any"; message?: string;
};
export type WorkflowDefinition = { schema_version: 1 | 2; parameters: Record<string, ValueSpec>; nodes: WorkflowNode[]; edges: { source: string; target: string; branch?: "true" | "false" | null }[]; allowed_tools: string[] };
export type Draft = { scenario_id: string; revision: number; workflow: WorkflowDefinition; layout: { positions: Record<string, { x: number; y: number }>; viewport?: Record<string, number> }; test_cases: TestCase[] };
export type TestCase = { id: string; title: string; inputs: Record<string, unknown>; expected_schema?: Record<string, unknown> | null };
export type Validation = { valid: boolean; issues: { code: string; message: string; node_id?: string | null; field?: string | null }[] };
export type Version = { id: string; version: number; kind: "legacy" | "workflow"; workflow: WorkflowDefinition; created_at: string };
export type Capabilities = { enabled: boolean; agents: { id: string; title: string; structured_output: boolean; database_connectors: string[]; run_sql_allowed: boolean }[]; database_connectors: { name: string; engine: string }[]; tools: { name: string; description: string }[] };
export type StepRun = { node_id: string; status: string; inputs: Record<string, unknown> | null; outputs: Record<string, unknown> | null; diagnostics: Record<string, unknown>; error: string | null; started_at: string | null; completed_at: string | null };
export type WorkflowRun = { id: string; scenario_id: string; scenario_title: string; version: number; conversation_id: string | null; status: string; is_test: boolean; result: Record<string, unknown> | null; error: string | null; steps: StepRun[]; created_at: string };
export type AiProposal = { base_revision: number; summary: string; workflow: WorkflowDefinition; validation: Validation };
export const terminalStatuses = new Set(["succeeded", "failed", "cancelled", "rejected"]);
export const workflowUrl = (path: string) => `/api/workflows/${path}`;

export class WorkflowApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

export async function workflowRequest<T>(path: string, method = "GET", body?: unknown, key?: string): Promise<T> {
  const response = await fetch(workflowUrl(path), { method, cache: "no-store", headers: {
    "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}),
  }, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = response.status === 204 ? "{}" : await response.text();
  let payload: Record<string, unknown> | unknown[];
  try { payload = JSON.parse(text); } catch {
    throw new WorkflowApiError(response.ok ? "Сервер вернул некорректный ответ. Повторите запрос" : `Ошибка сервера (${response.status}). Повторите запрос`, response.status, "invalid_server_response");
  }
  if (!response.ok) {
    const error = payload && !Array.isArray(payload) ? payload : {};
    const code = typeof error.code === "string" ? error.code : undefined;
    throw new WorkflowApiError(response.status === 409 && code === "scenario_revision_conflict" ? "Конфликт изменений. Загрузите актуальный черновик; ваши изменения сохранены на экране" : typeof error.detail === "string" ? error.detail : `Запрос не выполнен (${response.status}). Проверьте настройки и повторите`, response.status, code);
  }
  return payload as T;
}

export function startWorkflow(scenarioId: string, workspace: string, inputs: Record<string, unknown>, options: Record<string, unknown> = {}, key = crypto.randomUUID()) {
  return workflowRequest<{ run_id: string; run: WorkflowRun }>(`scenarios/${scenarioId}/runs`, "POST", { workspace_id: workspace, inputs, ...options }, key);
}

export const nodeLabels: Record<NodeType, string> = { input: "Параметры", sql: "SQL-запрос", python: "Python", llm: "LLM-агент", coding_task: "Разработка кода", condition: "Условие", parallel: "Параллельные ветки", join: "Объединение", approval: "Подтверждение", result: "Результат" };
export const valueTypes: ValueType[] = ["string", "number", "boolean", "json", "table", "file", "files"];
export function newNode(type: NodeType, id: string): WorkflowNode {
  const node: WorkflowNode = { id, type, title: nodeLabels[type], inputs: {}, outputs: {} };
  if (type === "sql") Object.assign(node, { connector_name: "", sql: "SELECT 1 AS value", row_limit: 5000, timeout_ms: 300000, outputs: { table: { type: "table" }, file: { type: "file" }, execution_id: { type: "string" }, row_count: { type: "number" } } });
  if (type === "python") Object.assign(node, { code: "# inputs содержит значения и пути к входным файлам\n# df = pd.read_excel(inputs['file'])\n# Сохраняйте файлы в /workspace/output\nresult = {'ok': True}", outputs: { result: { type: "json" }, files: { type: "files", required: false } } });
  if (type === "llm") Object.assign(node, { agent_id: "", instructions: "Проанализируй входные данные и объясни выводы", output_format: "text", allowed_tools: [], outputs: { result: { type: "string" } } });
  if (type === "coding_task") Object.assign(node, { root_agent_id: "", developer_agent_id: "", qa_agent_id: "", gitlab_connector_id: "", project_id: "", target_branch: "", environment_profile_id: "", database_connector_id: null, inputs: { task: { source: "constant", type: "string", value: "" } }, outputs: { run_id: { type: "string" }, status: { type: "string" }, report: { type: "json", required: false }, candidate: { type: "json", required: false }, mr_url: { type: "string", required: false } } });
  if (type === "condition") Object.assign(node, { rules: [{ input: "value", operator: "eq", value: true }], match: "all", inputs: { value: { source: "constant", type: "boolean", value: true } }, outputs: { branch: { type: "boolean" } } });
  if (type === "approval") node.message = "Проверьте результаты перед продолжением";
  return node;
}
