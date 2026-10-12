import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ScenarioDefinition } from "@/features/scenarios/scenariosApi";
import { ScenarioCatalog } from "./scenario-catalog";

const scenario: ScenarioDefinition = {
  id: "workflow", slug: "report", workspace_id: "default", owner_user_id: null,
  scope: "official", title: "Отчёт по городу", summary: "Магазины", description: "Выгрузка магазинов",
  execution_kind: "workflow", status: "published", version: 1, published_at: null, created_at: "", updated_at: "",
  input_schema: { properties: { city: { type: "string", title: "Город" }, source: { type: "file", title: "Файл" } }, required: ["city", "source"] },
  prompt_template: "Отчёт {{city}}", tool_policy: { allow_run_sql: true, sql_guard_required: true, allowed_tools: [], post_answer_checklist: [] },
  default_connector_bindings: { llm_connector_name: null, database_connector_name: null, run_sql: null },
};
const personalScenario: ScenarioDefinition = { ...scenario, id: "personal", scope: "personal", execution_kind: "workflow" };
function setup(item = scenario, overrides: Partial<ComponentProps<typeof ScenarioCatalog>> = {}) {
  const run = vi.fn();
  render(
    <ScenarioCatalog
      scenarios={[item]}
      loading={false}
      runningScenarioId={null}
      launchingScenarioId={null}
      authoringTarget={null}
      authoringPending={false}
      runDisabled={false}
      saving={false}
      archivingScenarioId={null}
      onRunScenario={run}
      onCreateScenario={vi.fn()}
      onEditScenario={vi.fn()}
      onArchiveScenario={vi.fn()}
      {...overrides}
    />
  );
  return run;
}

describe("scenario catalog launch flow", () => {
  it("opens workflow setup without asking for parameters or blocking required files", () => {
    const run = setup();
    expect(screen.queryByLabelText(/Город/)).not.toBeInTheDocument();
    expect(screen.queryByText("Предпросмотр сформированного запроса")).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "К запуску сценария" });
    expect(button).toHaveTextContent("К запуску");
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(run).toHaveBeenCalledWith("workflow", expect.any(Object), expect.any(String));
  });
  it("keeps legacy parameters and their validation in the catalog", () => {
    setup({ ...scenario, execution_kind: "legacy", input_schema: { properties: { city: { type: "string", title: "Город" } }, required: ["city"] } });
    const button = screen.getByRole("button", { name: "Запустить сценарий" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Город/), { target: { value: "Москва" } });
    expect(button).toBeEnabled();
    expect(screen.getByText("Отчёт Москва")).toBeInTheDocument();
  });
  it("does not offer to launch an unpublished workflow", () => {
    setup({ ...scenario, status: "draft" });
    expect(screen.getByRole("button", { name: "К запуску сценария" })).toBeDisabled();
    expect(screen.getByText("Сначала опубликуйте сценарий")).toBeInTheDocument();
  });
  it("shows a wait indicator on New until the editor target is resolved", () => {
    const create = vi.fn();
    render(
      <ScenarioCatalog
        scenarios={[scenario]}
        loading={false}
        runningScenarioId={null}
        launchingScenarioId={null}
        authoringTarget="create"
        authoringPending
        runDisabled={false}
        saving={false}
        archivingScenarioId={null}
        onRunScenario={vi.fn()}
        onCreateScenario={create}
        onEditScenario={vi.fn()}
        onArchiveScenario={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Мои сценарии" }));
    const createButton = screen.getByRole("button", { name: "Открываю редактор…" });
    expect(createButton).toBeDisabled();
    expect(createButton).toHaveTextContent("Открываю редактор…");
    expect(createButton).toHaveAttribute("aria-busy", "true");
    fireEvent.click(createButton);
    expect(create).not.toHaveBeenCalled();
  });
  it("keeps the launch indicator visible while navigating to the run page", () => {
    setup(personalScenario, { launchingScenarioId: personalScenario.id });
    fireEvent.click(screen.getByRole("button", { name: "Мои сценарии" }));
    expect(screen.getByRole("button", { name: /К запуску Отчёт по городу/ })).toBeDisabled();
    expect(screen.getAllByText("Открываю запуск…")).toHaveLength(2);
  });
  it("blocks launching while the chat turn is still running", () => {
    setup(personalScenario, { runDisabled: true });
    fireEvent.click(screen.getByRole("button", { name: "Мои сценарии" }));
    expect(screen.getByRole("button", { name: /К запуску Отчёт по городу/ })).toBeDisabled();
  });
});
