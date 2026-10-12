import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewWorkflowScenario } from "./NewWorkflowScenario";
import { createScenario, type ScenarioDefinition } from "./scenariosApi";
import { workflowRequest, type Capabilities } from "./workflowApi";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("./scenariosApi", () => ({ createScenario: vi.fn() }));
vi.mock("./workflowApi", async original => ({ ...await original<typeof import("./workflowApi")>(), workflowRequest: vi.fn() }));
const capabilities: Capabilities = { enabled: true, agents: [{ id: "agent", title: "Agent", structured_output: true, database_connectors: [], run_sql_allowed: true }], database_connectors: [{ name: "db", engine: "postgres" }], tools: [] };
beforeEach(() => vi.clearAllMocks());

describe("workflow creation", () => {
  it("creates an official draft with a SQL/Python/LLM graph and opens its editor", async () => {
    vi.mocked(createScenario).mockResolvedValue({ id: "official" } as ScenarioDefinition);
    vi.mocked(workflowRequest).mockResolvedValueOnce({ revision: 0 }).mockResolvedValueOnce({ revision: 1 });
    render(<NewWorkflowScenario workspace="workspace" capabilities={capabilities} initialScope="official" canCreateOfficial />);
    expect(screen.getByLabelText("Доступ")).toHaveValue("official");
    expect(screen.getByRole("link", { name: "← Администрирование" })).toHaveAttribute("href", "/admin");
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Общий анализ" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать и настроить" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/scenarios/official"));
    expect(createScenario).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ scope: "official", status: "draft", title: "Общий анализ", workspace_id: "workspace" }));
    expect(workflowRequest).toHaveBeenLastCalledWith("scenarios/official/draft", "PUT", expect.objectContaining({ expected_revision: 0, workflow: expect.objectContaining({ nodes: expect.arrayContaining([expect.objectContaining({ type: "python", inputs: { file: expect.objectContaining({ node_id: "sql", type: "file" }) } }), expect.objectContaining({ type: "llm", agent_id: "agent" })]) }) }));
  });

  it("keeps personal scope for a user who cannot create official scenarios", () => {
    render(<NewWorkflowScenario workspace="workspace" capabilities={capabilities} initialScope="official" />);
    expect(screen.getByLabelText("Доступ")).toHaveValue("personal");
    expect(screen.queryByRole("option", { name: /Общий/ })).not.toBeInTheDocument();
  });
});
