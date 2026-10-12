import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Bindings, ValueField } from "./WorkflowNodeSettings";
import { WorkflowProgress } from "./WorkflowProgress";
import { ancestors, insertStep, templateGraph } from "./workflowAuthoring";
import { workflowRequest, type Capabilities, type Draft } from "./workflowApi";

vi.mock("./monaco", () => ({}));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));
const capabilities: Capabilities = { enabled: true, agents: [{ id: "agent", title: "Agent", structured_output: true, database_connectors: [], run_sql_allowed: true }], database_connectors: [{ name: "db", engine: "postgres" }], tools: [] };
const draft = (): Draft => ({ scenario_id: "scenario", revision: 1, test_cases: [], ...templateGraph("analysis", "db", "agent") });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("guided workflow authoring", () => {
  it("connects the complete SQL file to Python and includes processed files in the result", () => {
    const { workflow } = draft();
    expect(workflow.nodes.find(node => node.id === "python")?.inputs.file).toMatchObject({ source: "step", node_id: "sql", path: ["file"], type: "file" });
    expect(workflow.nodes.find(node => node.id === "result")?.inputs.files).toMatchObject({ node_id: "python", path: ["files"], type: "files", required: false });
    expect(ancestors(workflow, "python").map(node => node.id)).toEqual(["input", "sql"]);
  });
  it("inserts a step without breaking existing downstream data references", () => {
    const original = draft();
    const inserted = insertStep(original, "sql", "python", capabilities);
    expect(inserted.draft.workflow.edges).toContainEqual({ source: "sql", target: inserted.id });
    expect(inserted.draft.workflow.edges).toContainEqual({ source: inserted.id, target: "python" });
    expect(inserted.draft.workflow.nodes.at(-1)?.inputs.file).toMatchObject({ node_id: "sql", type: "file" });
    expect(original.workflow.edges).toContainEqual({ source: "sql", target: "python" });
  });
  it("selects a prior output without JSON and lets the author rename its input", () => {
    function Settings() {
      const [node, setNode] = useState({ ...draft().workflow.nodes[2], inputs: {} });
      return <Bindings node={node} graph={draft().workflow} update={setNode} />;
    }
    render(<Settings />);
    const picker = screen.getByLabelText("Добавить данные предыдущего шага");
    expect(picker).not.toHaveTextContent("LLM-агент");
    fireEvent.change(picker, { target: { value: "sql:file" } });
    expect(screen.getByText("Файл · передаётся полный файл")).toBeInTheDocument();
    fireEvent.blur(screen.getByLabelText("Имя входа file"), { target: { value: "source_file" } });
    expect(screen.getByLabelText("Имя входа source_file")).toHaveValue("source_file");
  });
  it("stores numbers and booleans as typed values", () => {
    const number = vi.fn(); const boolean = vi.fn();
    render(<><ValueField type="number" value={12} label="Лимит" onChange={number} /><ValueField type="boolean" value={false} label="Активен" onChange={boolean} /></>);
    fireEvent.change(screen.getByLabelText("Лимит"), { target: { value: "1500" } });
    fireEvent.change(screen.getByLabelText("Активен"), { target: { value: "true" } });
    expect(number).toHaveBeenCalledWith(1500); expect(boolean).toHaveBeenCalledWith(true);
  });
  it("shows elapsed AI activity and clears its timer on unmount", () => {
    vi.useFakeTimers();
    const view = render(<WorkflowProgress label="AI готовит предложение" />);
    act(() => { vi.advanceTimersByTime(65000); });
    expect(screen.getByRole("status")).toHaveTextContent("1 мин 5 с");
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it("turns a text 500 into an actionable API error rather than a JSON syntax error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Internal Server Error", { status: 500 })));
    await expect(workflowRequest("scenarios/id/ai-proposals", "POST", {})).rejects.toMatchObject({ status: 500, code: "invalid_server_response", message: expect.stringContaining("Ошибка сервера (500)") });
  });
  it("preserves the provider's safe domain message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ code: "scenario_ai_invalid_output", detail: "Модель вернула неполное определение" }, { status: 422 })));
    await expect(workflowRequest("scenarios/id/ai-proposals", "POST", {})).rejects.toMatchObject({ status: 422, code: "scenario_ai_invalid_output", message: "Модель вернула неполное определение" });
  });
});
