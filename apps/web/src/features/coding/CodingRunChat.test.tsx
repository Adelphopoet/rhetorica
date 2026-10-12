import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CodingRunChat } from "./CodingRunChat";
import type { CodingChatEvent, CodingRun, CodingView } from "./codingApi";

vi.mock("@/app/components/chat-message", () => ({ ChatMessageComponent: (props: { assistantLabel: string; subagentRole?: string; text: string; toolCalls?: unknown[] }) => <article aria-label={props.assistantLabel}><span>{props.subagentRole}</span><p>{props.text}</p><output>{JSON.stringify(props.toolCalls)}</output></article> }));
afterEach(() => vi.unstubAllGlobals());
const run: CodingRun = { id: "run", status: "running", phase: "developing", deadline: "2026-10-02T14:00:00Z", created_at: "2026-10-02T13:00:00Z", repository: { project_id: "group/project", target_branch: "main", base_sha: "abc" } };
const event = (sequence: number, type: string, payload: CodingChatEvent["payload"]): CodingChatEvent => ({ sequence, type, task_id: "dev", created_at: "2026-10-02T13:00:00Z", payload });
function page(events: CodingChatEvent[], cursor: number): CodingView { return { run: { ...run, status: "failed" }, progress: { cursor, events, tasks: [{ id: "dev", role: "developer", status: "failed", iterations_used: 196, max_iterations: 200 }, { id: "qa", role: "qa", status: "queued", iterations_used: 0, max_iterations: 200 }], budgets: [] } }; }
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); render(<QueryClientProvider client={client}><CodingRunChat run={run} /></QueryClientProvider>); return client; }

it("drains event pages before terminal status and restores specialist tools and messages", async () => {
  const first = Array.from({ length: 200 }, (_, i) => event(i + 1, i === 199 ? "agent.tool_started" : "iteration.reserved", i === 199 ? { tool_name: "repository_read", tool_call_id: "read", arguments: { path: "models/example.sql" } } : {}));
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.endsWith("after=0") ? page(first, 200) : page([event(201, "agent.tool_completed", { tool_call_id: "read", success: true, result_summary: "Выполнено" }), event(202, "agent.message", { content: "Модель сохранена." })], 202)));
  vi.stubGlobal("fetch", fetch); mount();
  expect(await screen.findByText("Модель сохранена.")).toBeInTheDocument();
  expect(screen.getByLabelText("Developer — разработка")).toHaveTextContent('"success":true');
  expect(screen.getByLabelText("QA — независимая проверка")).toHaveTextContent("Не запускался");
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/coding/coding-runs/run?after=0", "/api/coding/coding-runs/run?after=200"]);
});

it("keeps saved messages on refresh and resumes after the last cursor", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(page([event(1, "agent.message", { content: "Готовлю тесты." })], 1))).mockResolvedValueOnce(Response.json(page([event(2, "agent.message", { content: "Тесты подготовлены." })], 2)));
  vi.stubGlobal("fetch", fetch); const client = mount();
  expect(await screen.findByText("Готовлю тесты.")).toBeInTheDocument();
  await client.invalidateQueries({ queryKey: ["coding-chat-progress", "run"] });
  await waitFor(() => expect(screen.getByLabelText("Developer — разработка")).toHaveTextContent("Тесты подготовлены."));
  expect(screen.getByLabelText("Developer — разработка")).toHaveTextContent("Готовлю тесты.");
  expect(fetch.mock.calls[1][0]).toBe("/api/coding/coding-runs/run?after=1");
});

it("collapses the team without losing its progress or fetching the journal again", async () => {
  const fetch = vi.fn().mockImplementation(async () => Response.json(page([event(1, "agent.message", { content: "Сохранённый отчёт участника." })], 1)));
  vi.stubGlobal("fetch", fetch); mount();
  expect(await screen.findByText("Сохранённый отчёт участника.")).toBeInTheDocument();
  const summary = screen.getByRole("button", { name: /Работа команды/ });
  expect(summary).toHaveTextContent("0/2 участников завершили работу");
  fireEvent.click(summary);
  expect(summary).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Сохранённый отчёт участника.")).not.toBeInTheDocument();
  fireEvent.click(summary);
  expect(screen.getByText("Сохранённый отчёт участника.")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("returns a created draft merge request to the chat", async () => {
  const state = page([], 0);
  state.run.status = "succeeded";
  state.receipt = {
    state: "mr_recorded",
    branch_name: "feature/rhetorica/run",
    merge_request_url: "https://gitlab.example.test/group/project/-/merge_requests/1",
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(state)));
  mount();

  const publication = await screen.findByLabelText("Публикация");
  expect(publication).toHaveTextContent("Команда завершила работу. Draft Merge Request создан.");
  expect(publication).toHaveTextContent("feature/rhetorica/run");
  expect(publication).toHaveTextContent("Ссылка на Draft MR");
  expect(publication).toHaveTextContent("https://gitlab.example.test/group/project/-/merge_requests/1");
  expect(publication).not.toHaveTextContent("Открыть Draft MR");
  fireEvent.click(screen.getByRole("button", { name: /Работа команды/ }));
  expect(screen.queryByLabelText("Developer — разработка")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Публикация")).toHaveTextContent("Ссылка на Draft MR");
});

it("does not render a publication message for an unsafe merge request URL", async () => {
  const state = page([], 0);
  state.receipt = {
    state: "mr_recorded",
    branch_name: "feature/rhetorica/run",
    merge_request_url: "javascript:alert(1)",
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(state)));
  mount();

  await screen.findByLabelText("Developer — разработка");
  expect(screen.queryByLabelText("Публикация")).not.toBeInTheDocument();
});

it("shows the coordinator like a member, with assignments as its execution log", async () => {
  const team = { team_id: "team", team_name: "dbt team", revision: 1, members: [{ id: "engineer", agent_profile_id: "dev", label: "Data engineer", capability: "candidate" as const }] };
  const view: CodingView = { run: { ...run, status: "failed", team_snapshot: team }, progress: { cursor: 3, budgets: [], tasks: [{ id: "coord", role: "coordinator", status: "succeeded", iterations_used: 3, max_iterations: 200 }, { id: "dev", role: "developer", team_member_label: "Data engineer", status: "succeeded", iterations_used: 5, max_iterations: 100 }], events: [
    { sequence: 1, task_id: "dev", type: "team.member_started", created_at: run.created_at, payload: { event_key: "call-1", member_label: "Data engineer", assignment: "Создай модель", assignment_truncated: true } },
    { sequence: 2, task_id: "dev", type: "team.member_completed", created_at: run.created_at, payload: { event_key: "call-1", member_label: "Data engineer", status: "candidate_recorded", summary: "Готово" } },
    { sequence: 3, task_id: "coord", type: "team.coordinator_decision", created_at: run.created_at, payload: { decision: "stop", summary: "Нужен build" } },
  ] } };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json(view))); mount();
  const coordinator = await screen.findByLabelText("Координатор — команда «dbt team», ревизия 1");
  expect(coordinator).toHaveTextContent("Решение: остановить работу.");
  expect(coordinator).toHaveTextContent("Нужен build");
  expect(coordinator).toHaveTextContent('"tool_name":"Data engineer"');
  expect(coordinator).toHaveTextContent("candidate сохранён: Готово");
  expect(coordinator).toHaveTextContent("участнику передано полностью");
});


it.each(["not_run", "failed", "blocked", "passed"] as const)("explains required review %s in the coordinator message without event history", async status => {
  const state = page([], 0);
  state.run.reason_code = "policy_denied";
  state.run.team_snapshot = { team_id: "team", team_name: "dbt", revision: 1, members: [
    { id: "sql", agent_profile_id: "sql", label: "SQL reviewer", capability: "review", required_review: true },
    { id: "perf", agent_profile_id: "perf", label: "Performance reviewer", capability: "review", required_review: true },
    { id: "optional", agent_profile_id: "extra", label: "Optional reviewer", capability: "review" },
  ] };
  state.report = { checks: [
    { name: "independent_review:sql", status: "passed", mandatory: true },
    { name: "independent_review:perf", status, mandatory: true },
  ] };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json(state)));
  mount();
  const message = await screen.findByLabelText("Координатор — команда «dbt», ревизия 1");
  const expected = { not_run: "Не выполнена", failed: "Не пройдена", blocked: "Заблокирована" };
  if (status === "passed") expect(message).not.toHaveTextContent("обязательная проверка");
  else expect(message).toHaveTextContent(`${expected[status]} обязательная проверка: Performance reviewer`);
  expect(message).not.toHaveTextContent("SQL reviewer");
  expect(message).not.toHaveTextContent("Optional reviewer");
});
