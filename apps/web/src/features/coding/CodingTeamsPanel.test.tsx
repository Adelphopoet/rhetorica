import { selectOption } from "@/test/selectOption";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentProfile } from "@/features/agents/agentsApi";
import { CodingTeamsPanel } from "./CodingTeamsPanel";
import { CodingRunView } from "./CodingRunView";
import { missingCodingSettings, type CodingTeamDetail, type CodingView } from "./codingApi";

const agent = (id: string, overrides: Partial<AgentProfile> = {}): AgentProfile => ({
  id, workspace_id: "workspace", slug: id, title: id, description: null, is_active: true, llm_connector_name: null,
  allowed_database_connector_names: [], allowed_rag_connector_names: [], allowed_documentation_connector_names: [], allowed_gitlab_connector_names: ["gitlab"],
  allowed_mcp_connector_names: [], allowed_airflow_connector_names: [], allowed_kubernetes_connector_names: [], allowed_victoriametrics_connector_names: [],
  allowed_workspace_skill_ids: ["dbt-core"], settings: { run_sql_allowed: true, temperature: null, max_tokens: null }, star_count: 0, is_starred: false, is_favorite: false,
  created_at: "2026-01-01", updated_at: "2026-01-01", ...overrides,
});
const agents = [agent("coordinator"), agent("developer"), agent("reviewer")];
const skills = [{ id: "dbt-core", name: "dbt-core", title: "dbt core", version_digest: "v1" }];
const revision = { id: "revision-1", team_id: "team-1", revision: 1, coordinator_agent_profile_id: "coordinator", coordinator_instruction: "", is_active: true, created_at: "2026-01-01T00:00:00Z", members: [
  { id: "engineer", agent_profile_id: "developer", label: "Data engineer", capability: "candidate" as const, instruction: "", skill_version_bindings: [] },
  { id: "reviewer", agent_profile_id: "reviewer", label: "Reviewer", capability: "review" as const, instruction: "", skill_version_bindings: [] },
] };
const team: CodingTeamDetail = { team: { id: "team-1", name: "dbt team", active_revision_id: "revision-1", latest_revision: 1, updated_at: "2026-01-01" }, revisions: [revision] };
afterEach(() => vi.unstubAllGlobals());

async function fillMember(id: string, label: string, profile: string) {
  fireEvent.click(screen.getByLabelText(`Настроить участника ${id}`));
  fireEvent.change(screen.getByLabelText(`Имя участника ${id}`), { target: { value: label } });
  await selectOption(`Профиль участника ${id}`, profile);
}

describe("coding team administration", () => {
  it("creates and activates a team with member instructions and skills", async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.endsWith("/coding-agent-teams")) return Response.json({ ...revision, id: "revision-new" }, { status: 201 });
      if (init?.method === "POST") return Response.json(team.team);
      return Response.json([team]);
    });
    vi.stubGlobal("fetch", fetch);
    render(<CodingTeamsPanel initialTeams={[]} agents={agents} skills={skills} />);
    fireEvent.click(screen.getByRole("button", { name: /Новая команда/ }));
    fireEvent.change(screen.getByLabelText("Название команды"), { target: { value: "dbt team" } });
    await selectOption("Координатор", "coordinator");
    await fillMember("member-1", "Data engineer", "developer");
    fireEvent.change(screen.getByLabelText("Инструкция участника member-1"), { target: { value: "Check dbt changes" } });
    fireEvent.click(within(screen.getByRole("listitem", { name: "Участник Data engineer" })).getByRole("checkbox", { name: "dbt core" }));
    await fillMember("member-2", "Reviewer", "reviewer");
    fireEvent.click(screen.getByLabelText("Обязательная проверка member-2"));
    fireEvent.click(screen.getByRole("button", { name: "Создать команду" }));

    await waitFor(() => expect(screen.getByText(/Ревизия 1 сохранена и активирована/)).toBeInTheDocument());
    const created = fetch.mock.calls.find(([url, init]) => String(url).endsWith("/coding-agent-teams") && init?.method === "POST")!;
    expect(JSON.parse(String(created[1]?.body))).toMatchObject({
      expected_revision: 0, name: "dbt team", coordinator_agent_profile_id: "coordinator",
      members: [
        { id: "member-1", label: "Data engineer", capability: "candidate", instruction: "Check dbt changes", skill_version_bindings: [{ skill_id: "dbt-core", version_digest: "v1" }] },
        { id: "member-2", label: "Reviewer", capability: "review", required_review: true },
      ],
    });
    expect(fetch).toHaveBeenCalledWith("/api/coding/coding-agent-teams/team-1/revisions/revision-new/activate", expect.objectContaining({ method: "POST" }));
    expect(screen.getByRole("list", { name: "Участники dbt team" })).toHaveTextContent("Data engineer");
  });

  it("shows missing grants and coordinator membership before saving", async () => {
    const restricted = [...agents.slice(0, 2), agent("reviewer", { allowed_gitlab_connector_names: [] })];
    render(<CodingTeamsPanel initialTeams={[team]} agents={restricted} skills={skills} />);
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    expect(screen.getByText("Нет разрешённого GitLab-коннектора")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить новую ревизию" })).toBeDisabled();

    fireEvent.click(screen.getByLabelText("Настроить участника reviewer"));
    await selectOption("Профиль участника reviewer", "coordinator");
    expect(screen.getByText("Координатор не может быть участником")).toBeInTheDocument();
  });

  it("keeps member edits when collapsed and reopened without saving", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<CodingTeamsPanel initialTeams={[team]} agents={agents} skills={skills} />);
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    const summary = screen.getByLabelText("Настроить участника engineer");
    const details = summary.closest("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    fireEvent.change(screen.getByLabelText("Инструкция участника engineer"), { target: { value: "Проверить модель" } });
    fireEvent.click(summary);
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(screen.getByLabelText("Инструкция участника engineer")).toHaveValue("Проверить модель");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("names the member rejected by server validation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: { code: "coding_team_delegation_not_allowed", member_id: "reviewer", member_label: "Reviewer" } }, { status: 422 })));
    render(<CodingTeamsPanel initialTeams={[team]} agents={agents} skills={skills} />);
    fireEvent.click(screen.getByRole("button", { name: "Изменить" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить новую ревизию" }));
    const card = await screen.findByRole("listitem", { name: "Участник Reviewer" });
    expect(within(card).getByRole("alert")).toHaveTextContent("Координатору не разрешён вызов участника: Reviewer");
  });
});

describe("team run observability", () => {
  it("renders member labels, assignments and per-reviewer checks", async () => {
    const state: CodingView = {
      run: { id: "run-1", status: "running", phase: "developing", deadline: "2099-01-01", created_at: "2026-01-01", repository: { project_id: "group/project", target_branch: "main", base_sha: "a".repeat(40) },
        team_snapshot: { team_id: "team-1", team_name: "dbt team", revision: 3, members: revision.members } },
      report: { checks: [{ name: "independent_review:reviewer", status: "failed", mandatory: true, reason: "refunds missing" }] },
      progress: { cursor: 3, tasks: [{ id: "t1", role: "developer", team_member_label: "Data engineer", status: "succeeded", iterations_used: 1, max_iterations: 5 }], budgets: [], events: [
        { sequence: 1, task_id: "t1", type: "team.member_started", created_at: "2026-01-01", payload: { member_label: "Data engineer", capability: "candidate", assignment: "Add net_revenue" } },
        { sequence: 2, task_id: "t1", type: "team.member_completed", created_at: "2026-01-01", payload: { member_label: "Data engineer", status: "candidate_recorded", candidate_digest: "b".repeat(64) } },
        { sequence: 3, task_id: "t2", type: "team.member_completed", created_at: "2026-01-01", payload: { member_label: "Reviewer", status: "failed", summary: "refunds missing" } },
      ] },
    };
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(state)));
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><CodingRunView runId="run-1" /></QueryClientProvider>);

    expect(await screen.findByText(/Команда: dbt team · ревизия 3/)).toBeInTheDocument();
    const timeline = await screen.findByRole("list", { name: "Поручения команды" });
    expect(timeline).toHaveTextContent("Поручение: Add net_revenue");
    expect(timeline).toHaveTextContent("Reviewer: найдены замечания");
    expect(screen.getByText("Проверка участника · Reviewer")).toBeInTheDocument();
    expect(screen.getByText(/Data engineer · succeeded/)).toBeInTheDocument();
  });

  it("requires an available team instead of fixed Developer/QA in the preset", () => {
    const base = { gitlab_connector_id: "gitlab", project_id: "p", target_branch: "main", environment_profile_id: "env" };
    expect(missingCodingSettings({ ...base, coding_team_id: null })).toContain("Команда coding-агентов");
    expect(missingCodingSettings({ ...base, coding_team_id: "team" })).toEqual([]);
    expect(missingCodingSettings({ ...base, developer_agent_id: "", qa_agent_id: "" })).toEqual(["Developer", "QA"]);
  });
});
