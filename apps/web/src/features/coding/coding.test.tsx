import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodingRunView } from "./CodingRunView";
import { type CodingView } from "./codingApi";
import { templateGraph } from "@/features/scenarios/workflowAuthoring";

function wrap(ui: React.ReactNode) { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return { ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>), client }; }
const view = (status = "running"): CodingView => ({ run: { id: "run-1", status, phase: "validating", deadline: "2099-01-01", created_at: "2026-01-01", repository: { project_id: "group/project", target_branch: "main", base_sha: "a".repeat(40) } }, progress: { cursor: 7, tasks: [], budgets: [] } });
class Stream {
  static instances: Stream[] = [];
  handlers = new Map<string, EventListener>();
  close = vi.fn();
  constructor(public url: string) { Stream.instances.push(this); }
  addEventListener(name: string, fn: EventListener) { this.handlers.set(name, fn); }
  emit(sequence: string) { this.handlers.get("coding")?.({ lastEventId: sequence } as unknown as Event); }
}
afterEach(() => { vi.unstubAllGlobals(); Stream.instances = []; });

describe("governed coding web entry", () => {
  it("ships an unconfigured v2 dbt template without grants or resource guesses", () => {
    const graph = templateGraph("dbt", "existing-db", "existing-agent").workflow;
    expect(graph.schema_version).toBe(2);
    expect(graph.nodes[1]).toMatchObject({ type: "coding_task", developer_agent_id: "", qa_agent_id: "", gitlab_connector_id: "", project_id: "", target_branch: "" });
    expect(templateGraph("agent", "db", "agent").workflow.schema_version).toBe(1);
  });
  it.each(["base_changed", "gitlab_connector_scope_insufficient"])("explains %s with not_run and partial publication", async reason => {
    const state = view("failed"); state.run.reason_code = reason;
    state.report = { checks: [{ name: "materialization", status: "not_run", mandatory: false }] };
    state.receipt = { state: "commit_created", commit_sha: "commit", branch_name: "coding/run", reason_code: reason };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(state))); vi.stubGlobal("EventSource", Stream);
    wrap(<CodingRunView runId="run-1" />);
    expect(await screen.findByText(/not_run/)).toHaveTextContent("Не выполнялось");
    expect(screen.getByText(/Commit: commit/)).toBeInTheDocument();
    expect(screen.getByText(/частичный receipt сохранён/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить задачу" })).not.toBeInTheDocument();
  });
  it("restores the persisted run on refresh and cancels through its owner endpoint", async () => {
    let state = view();
    const fetch = vi.fn(async (url: string) => { if (url.endsWith("/cancel")) state = view("cancelled"); return Response.json(state); });
    vi.stubGlobal("fetch", fetch); vi.stubGlobal("EventSource", Stream);
    const first = wrap(<CodingRunView runId="run-1" />); await screen.findByText("group/project"); first.unmount();
    wrap(<CodingRunView runId="run-1" />); fireEvent.click(await screen.findByRole("button", { name: "Отменить задачу" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Отменить задачу" })).not.toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith("/api/coding/coding-runs/run-1/cancel", expect.objectContaining({ method: "POST" }));
  });
  it("ignores duplicate and out-of-order events and does not cancel on disconnect", async () => {
    const fetch = vi.fn().mockImplementation(async () => Response.json(view())); vi.stubGlobal("fetch", fetch); vi.stubGlobal("EventSource", Stream);
    const rendered = wrap(<CodingRunView runId="run-1" />); await screen.findByText("group/project");
    const stream = Stream.instances.at(-1)!;
    await act(async () => stream.emit("9")); const calls = fetch.mock.calls.length;
    await act(async () => { stream.emit("8"); stream.emit("9"); });
    expect(fetch.mock.calls.length).toBe(calls);
    rendered.unmount(); expect(stream.close).toHaveBeenCalled();
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/cancel"))).toBe(false);
  });
});


it("names skipped mandatory reviews on the run and distinguishes them from passed checks", async () => {
  const state = view("failed");
  state.run.reason_code = "policy_denied";
  state.run.team_snapshot = { team_id: "team", team_name: "dbt", revision: 1, members: [
    { id: "sql", agent_profile_id: "sql-agent", label: "SQL reviewer", capability: "review", required_review: true },
    { id: "perf", agent_profile_id: "perf-agent", label: "Performance reviewer", capability: "review", required_review: true },
  ] };
  state.report = { checks: [
    { name: "independent_review:sql", status: "passed", mandatory: true },
    { name: "independent_review:perf", status: "not_run", mandatory: true, reason: "Не выполнена обязательная проверка: Performance reviewer" },
  ] };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => Response.json(state)));
  vi.stubGlobal("EventSource", Stream);
  const first = wrap(<CodingRunView runId="run-1" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Не выполнена обязательная проверка: Performance reviewer");
  expect(screen.getByText("Проверка участника · Performance reviewer").closest("li")).toHaveTextContent("Не выполнялось");
  expect(screen.getByText("Проверка участника · SQL reviewer").closest("li")).toHaveTextContent("Пройдено");
  first.unmount();
  wrap(<CodingRunView runId="run-1" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Performance reviewer");
});
