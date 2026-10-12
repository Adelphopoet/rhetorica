import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunResult, WorkflowRunView } from "./WorkflowRunView";
import { workflowRequest, type WorkflowRun } from "./workflowApi";

vi.mock("./workflowApi", async importOriginal => ({ ...await importOriginal<typeof import("./workflowApi")>(), workflowRequest: vi.fn() }));
vi.mock("@/app/components/chat-message", () => ({ ChatMessageComponent: ({ text, files }: { text: string; files: { id: string; filename: string }[] }) => <div>{text}{files.map(file => <a key={file.id} href={`/files/${file.id}`}>{file.filename}</a>)}</div> }));
vi.mock("@/app/components/result-table", () => ({ ResultTable: () => null }));
vi.mock("@/features/charts", () => ({ VisualizationChart: () => null }));

class Events {
  static current: Events;
  listeners = new Map<string, () => void>();
  close = vi.fn();
  constructor() { Events.current = this; }
  addEventListener(name: string, callback: () => void) { this.listeners.set(name, callback); }
}

const waitingRun = (): WorkflowRun => ({ id: "run", scenario_id: "scenario", scenario_title: "Проверка файла", version: 2, conversation_id: "chat", status: "waiting", is_test: false, result: null, error: null, created_at: "2026-09-08", steps: [{ node_id: "approval", status: "waiting", inputs: {}, outputs: null, diagnostics: { message: "Проверьте полный SQL-файл" }, error: null, started_at: null, completed_at: null }] });

beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal("EventSource", Events); });

describe("scenario execution", () => {
  it("continues an approval and updates its state through the run API", async () => {
    let run = waitingRun();
    vi.mocked(workflowRequest).mockImplementation(async (_path, method, body) => {
      if (method === "POST") {
        expect(body).toEqual({ workspace_id: "default", approved: true });
        run = { ...run, status: "running", steps: [{ ...run.steps[0], status: "succeeded" }] };
        return undefined as never;
      }
      return run as never;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><WorkflowRunView runId="run" workspace="default" /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Продолжить" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Продолжить" })).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Выполняется");
    view.unmount();
    expect(Events.current.close).toHaveBeenCalled();
  });

  it("refreshes from SSE after a background failure and closes the subscription", async () => {
    let run = waitingRun();
    vi.mocked(workflowRequest).mockImplementation(async () => run as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkflowRunView runId="run" workspace="default" /></QueryClientProvider>);
    await screen.findByText("Проверьте полный SQL-файл");
    run = { ...run, status: "failed", error: "Доступ к агенту отозван", steps: [] };
    await act(async () => { Events.current.listeners.get("scenario")?.(); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Доступ к агенту отозван");
    expect(Events.current.close).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Отменить" })).not.toBeInTheDocument();
  });

  it("shows a shared artifact once when it occurs in both a table manifest and files", () => {
    const file = { id: "file", filename: "processed.csv" };
    render(<RunResult value={{ answer_markdown: "1200 строк", table: { file }, files: [file] }} />);
    expect(screen.getAllByRole("link", { name: "processed.csv" })).toHaveLength(1);
    expect(screen.getByText("1200 строк")).toBeInTheDocument();
  });
});


describe("execution page navigation and progress", () => {
  it("keeps the home link available when loading the run fails", async () => {
    vi.mocked(workflowRequest).mockRejectedValue(new Error("Запуск недоступен"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkflowRunView runId="run" workspace="default" /></QueryClientProvider>);
    expect(screen.getByRole("link", { name: "На главную" })).toHaveAttribute("href", "/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Запуск недоступен");
    expect(screen.getByRole("button", { name: "Повторить загрузку" })).toBeEnabled();
  });

  it("uses published step titles and focuses the active step", async () => {
    const run = { ...waitingRun(), status: "running", steps: [
      { ...waitingRun().steps[0], node_id: "python", status: "running" },
      { ...waitingRun().steps[0], node_id: "input", status: "succeeded" },
    ] };
    vi.mocked(workflowRequest).mockImplementation(async path => (path.includes("/versions?") ? [{ version: 2, workflow: { nodes: [{ id: "input", title: "Параметры" }, { id: "python", title: "Обработка файла" }, { id: "result", title: "Результат" }] } }] : run) as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkflowRunView runId="run" workspace="default" /></QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: "Обработка файла" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Шаги выполнения" })).getAllByRole("button")[0]).toHaveTextContent("Параметры");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "33");
    expect(screen.getByRole("button", { name: /Обработка файла/ })).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: /Параметры/ }));
    expect(screen.getByRole("heading", { name: "Параметры" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Новый запуск" })).toHaveAttribute("href", "/scenarios/scenario/run");
  });
});
