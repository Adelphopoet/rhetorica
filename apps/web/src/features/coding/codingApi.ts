export type CodingTeamCapability = "candidate" | "review";
export type CodingTeamMember = { id: string; agent_profile_id: string; label: string; instruction?: string; capability: CodingTeamCapability; required_review?: boolean; skill_version_bindings?: { skill_id: string; version_digest: string }[] };
export type CodingTeamRevision = { id: string; team_id: string; revision: number; coordinator_agent_profile_id: string; coordinator_instruction: string; members: CodingTeamMember[]; is_active: boolean; created_at: string; created_by?: string | null };
export type CodingTeam = { id: string; name: string; active_revision_id: string | null; latest_revision: number; updated_at: string };
export type CodingTeamDetail = { team: CodingTeam; revisions: CodingTeamRevision[] };
export type CodingTeamOption = { id: string; name: string; available: boolean; revision?: number; coordinator_agent_profile_id?: string; unavailable_reason?: string | null; unavailable_member?: string | null; members?: { id: string; label: string; capability: CodingTeamCapability; agent_profile_id?: string }[] };
export type CodingTeamSnapshot = { team_id: string; team_name: string; revision: number; members: CodingTeamMember[]; legacy?: boolean };
export type CodingResources = {
  launch_defaults?: CodingSelection | null;
  root_agent_id?: string | null;
  defaults_unavailable_reason?: string | null;
  defaults_unavailable_member?: string | null;
  teams?: CodingTeamOption[];
  workspace_defaults_revision?: number;
  enabled: boolean;
  can_start_coding?: boolean;
  specialists: { id: string; title: string; gitlab_connectors: string[]; database_connectors: string[] }[];
  gitlab_connectors: { id: string; name: string }[];
  database_connectors: { id: string; name: string }[];
  environments: { id: string; revision: number }[];
};
export type CodingSelection = { dbt_project_subdir?: string; root_agent_id?: string | null; coding_team_id?: string | null; developer_agent_id?: string | null; qa_agent_id?: string | null; gitlab_connector_id: string; project_id: string; target_branch: string; environment_profile_id: string; database_connector_id?: string | null };
export type CodingRun = { id: string; status: string; phase: string; conversation_id?: string | null; team_snapshot?: CodingTeamSnapshot | null; reason_code?: string | null; deadline: string; created_at: string; repository: { project_id: string; target_branch: string; base_sha: string }; candidate?: { digest: string; changed_paths: string[] } | null; partial_result?: { summary?: string | null } | null };
export type CodingCheck = { name: string; status: "passed" | "failed" | "blocked" | "not_run"; reason?: string | null; mandatory: boolean; exit_code?: number | null };
export type CodingView = { run: CodingRun; report?: { checks: CodingCheck[] } | null; receipt?: { state: string; commit_sha?: string | null; merge_request_url?: string | null; branch_name: string; reason_code?: string | null } | null; progress: { events?: CodingChatEvent[]; cursor: number; tasks: { id: string; parent_task_id?: string | null; role: string; team_member_id?: string | null; team_member_label?: string | null; status: string; iterations_used: number; max_iterations: number; deadline?: string | null }[]; budgets: { budget_owner_agent_id: string; reserved_iterations: number; max_iterations: number; deadline: string }[] } };
export const terminalCodingStatuses = new Set(["succeeded", "failed", "cancelled", "rejected"]);
export const codingReasons: Record<string, string> = {
  coding_plan_superseded: "План изменён. Подтверди актуальный план в чате", coding_confirmation_required: "Сначала обсуди и подтверди план разработки в чате", coding_resources_required: "Выбери ресурсы в настройках coding", coding_planning_attachments_not_supported: "Опиши задачу текстом без вложений",
  coding_defaults_revision_conflict: "Настройки workspace уже изменены. Обнови страницу",
  coding_workspace_coordinator_required: "Выберите coding-координатора",
  workspace_owner_access_denied: "Настройки меняет владелец workspace или администратор",
  coding_dependency_install_failed: "Не удалось установить Python-зависимости. Проверьте пакеты и HTTPS-индекс в Coding environments",
  coding_disabled: "Запуски разработки отключены", channel_not_allowed: "Разработка доступна только из веб-интерфейса",
  coding_delegation_not_allowed: "Координатору не разрешён вызов выбранных специалистов", publication_not_authorized: "Код подготовлен, публикация не разрешена политикой",
  base_changed: "Целевая ветка изменилась после старта — нужен новый запуск", gitlab_connector_scope_insufficient: "У GitLab-токена недостаточно прав для создания Draft MR",
  coding_gitlab_access_denied: "Специалистам не разрешён выбранный GitLab-коннектор", coding_database_access_denied: "Специалистам не разрешён выбранный коннектор DWH",
  cancelled_by_owner: "Задача отменена пользователем", iteration_limit_reached: "Исчерпан бюджет итераций", runtime_limit_reached: "Истёк срок выполнения",
  independent_qa_agent_required: "Выберите отдельного QA-агента", sandbox_runtime_unavailable: "Окружение исполнения недоступно", execution_failed: "Ошибка исполнения — сохранён частичный результат",
  coding_team_required: "Выберите команду coding-агентов", coding_team_inactive: "У команды нет активной ревизии", coding_team_not_found: "Команда coding-агентов не найдена",
  coding_team_coordinator_mismatch: "Команда принадлежит другому координатору", coding_team_coordinator_unavailable: "Координатор команды недоступен",
  coding_team_member_unavailable: "Профиль участника недоступен или отключён", coding_team_delegation_not_allowed: "Координатору не разрешён вызов участника",
  coding_team_member_gitlab_grant_missing: "У участника нет разрешённого GitLab-коннектора", coding_team_skill_not_allowed: "Skill не разрешён профилю участника",
  coding_team_skill_version_unavailable: "Выбранная версия skill недоступна", coding_team_revision_conflict: "Команда уже изменена. Обнови страницу",
  coding_team_name_conflict: "Команда с таким названием уже есть", coding_team_revision_not_found: "Ревизия команды не найдена",
  coding_agent_unavailable: "Профиль агента недоступен", admin_access_required: "Команды меняет системный администратор",
  coordinator_stopped: "Координатор остановил работу без публикации", policy_denied: "Публикация отклонена проверками", resource_access_revoked: "Доступ к ресурсу отозван во время работы",
};
export const codingCapabilityLabels: Record<CodingTeamCapability, string> = { candidate: "Создаёт изменения", review: "Независимая проверка" };
export function codingReason(reason?: string | null, member?: string | null) {
  if (!reason) return "";
  const text = codingReasons[reason] ?? reason;
  return member ? `${text}: ${member}` : text;
}
export function codingErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return codingReason(detail);
  if (detail && typeof detail === "object" && "code" in detail) {
    const value = detail as { code: string; member_label?: string | null };
    return codingReason(value.code, value.member_label);
  }
  if (Array.isArray(detail)) return detail.map(item => (item as { msg?: string }).msg).filter(Boolean).join("; ") || "Запрос не выполнен";
  return "Запрос не выполнен";
}
export class CodingRequestError extends Error {
  constructor(message: string, readonly status: number, readonly detail: unknown) { super(message); }
  get memberId(): string | null {
    return this.detail && typeof this.detail === "object" && "member_id" in this.detail ? String((this.detail as { member_id: unknown }).member_id) : null;
  }
}
export async function codingRequest<T>(path: string, method = "GET", body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/coding/${path}`, { method, cache: "no-store", headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const payload = await response.json().catch(() => ({ detail: `Ошибка сервера (${response.status})` }));
  if (!response.ok) throw new CodingRequestError(codingErrorDetail(payload.detail), response.status, payload.detail);
  return payload;
}
export function missingCodingSettings(selection: CodingSelection, resources?: CodingResources): string[] {
  const missing: string[] = [];
  // Scenario steps still select Developer/QA directly; an empty string keeps that legacy form.
  const legacy = !selection.coding_team_id && (selection.developer_agent_id != null || selection.qa_agent_id != null);
  const required = [["gitlab_connector_id", "GitLab-коннектор"], ["project_id", "Проект GitLab"], ["target_branch", "Целевая ветка"], ["environment_profile_id", "Окружение"]] as const;
  if (legacy) {
    for (const [key, label] of [["developer_agent_id", "Developer"], ["qa_agent_id", "QA"]] as const) if (!selection[key]?.trim()) missing.push(label);
    if (selection.developer_agent_id && selection.qa_agent_id === selection.developer_agent_id) missing.push("Отдельный QA-агент");
  } else if (!selection.coding_team_id) missing.push("Команда coding-агентов");
  for (const [key, label] of required) if (!selection[key]?.trim()) missing.push(label);
  if (resources) {
    const { specialists = [], gitlab_connectors = [], database_connectors = [], environments = [], teams = [] } = resources;
    const team = teams.find(t => t.id === selection.coding_team_id);
    const memberIds = legacy ? [selection.developer_agent_id, selection.qa_agent_id].filter(Boolean) as string[] : (team?.members ?? []).map(m => m.agent_profile_id).filter(Boolean) as string[];
    if (selection.coding_team_id && !team) missing.push("Доступная команда coding-агентов");
    if (team && !team.available) missing.push(codingReason(team.unavailable_reason, team.unavailable_member) || "Доступная команда coding-агентов");
    for (const id of memberIds) if (!specialists.some(a => a.id === id)) missing.push("Разрешение на вызов специалиста");
    const selected = specialists.filter(a => memberIds.includes(a.id));
    const gitlab = gitlab_connectors.find(c => c.id === selection.gitlab_connector_id);
    if (selection.gitlab_connector_id && (!gitlab || selected.some(a => !a.gitlab_connectors.includes(gitlab.name)))) missing.push("Доступ к GitLab-коннектору");
    const database = database_connectors.find(c => c.id === selection.database_connector_id);
    if (selection.database_connector_id && (!database || selected.some(a => !a.database_connectors.includes(database.name)))) missing.push("Доступ к DWH");
    if (selection.environment_profile_id && !environments.some(e => e.id === selection.environment_profile_id)) missing.push("Доступное окружение");
    if (!resources.enabled) missing.push("Включение coding capability");
  }
  return [...new Set(missing)];
}

export type CodingChatEvent = { sequence: number; task_id?: string | null; type: string; created_at: string; payload: { tool_name?: string; tool_call_id?: string; arguments?: Record<string, unknown>; result_summary?: string; success?: boolean; content?: string; truncated?: boolean; member_id?: string; member_label?: string; capability?: CodingTeamCapability; assignment?: string; assignment_truncated?: boolean; event_key?: string; status?: string; summary?: string; candidate_digest?: string | null; decision?: "publish" | "stop" } };
export function codingCheckLabel(name: string, team?: CodingTeamSnapshot | null) {
  if (!name.startsWith("independent_review:")) return name;
  const memberId = name.slice("independent_review:".length);
  return `Проверка участника · ${team?.members.find(m => m.id === memberId)?.label ?? memberId}`;
}
export function codingPublicationReviewIssues(view: CodingView): string[] {
  if (view.run.reason_code !== "policy_denied" || !terminalCodingStatuses.has(view.run.status)) return [];
  const team = view.run.team_snapshot;
  if (!team || team.legacy) return [];
  return team.members.filter(member => member.required_review).flatMap(member => {
    const check = view.report?.checks.find(item => item.name === `independent_review:${member.id}`);
    if (check?.status === "passed") return [];
    const reason = !check || check.status === "not_run" ? "Не выполнена обязательная проверка"
      : check.status === "blocked" ? "Заблокирована обязательная проверка" : "Не пройдена обязательная проверка";
    return [`${reason}: ${member.label}`];
  });
}
export function codingTaskLabel(task: { role: string; team_member_label?: string | null }) {
  if (task.team_member_label) return task.team_member_label;
  return ({ coordinator: "Координатор", developer: "Developer", qa: "QA" } as Record<string, string>)[task.role] ?? task.role;
}
