import { selectOption } from "@/test/selectOption";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceCodingDefaultsPanel } from "./WorkspaceCodingDefaultsPanel";

const selection = { root_agent_id: "root", coding_team_id: "team", developer_agent_id: "dev", qa_agent_id: "qa", gitlab_connector_id: "gitlab", project_id: "group/project", target_branch: "main", environment_profile_id: "env" };
const view = { settings: { workspace_id: "workspace", revision: 7, resources: selection }, coordinators: [{ id: "root", title: "Coding coordinator" }] };
const team = { id: "team", name: "dbt team", available: true, revision: 3, members: [{ id: "engineer", label: "Data engineer", capability: "candidate", agent_profile_id: "dev" }, { id: "reviewer", label: "Reviewer", capability: "review", agent_profile_id: "qa" }] };
const resources = { enabled: true, can_start_coding: true, launch_defaults: selection, teams: [team, { id: "broken", name: "Old team", available: false, unavailable_reason: "coding_delegation_not_allowed", unavailable_member: "Reviewer" }], specialists: ["dev", "qa"].map(id => ({ id, title: id, gitlab_connectors: ["gitlab"], database_connectors: [] })), gitlab_connectors: [{ id: "gitlab", name: "gitlab" }], database_connectors: [], environments: [{ id: "env", revision: 1 }] };
afterEach(() => vi.unstubAllGlobals());
function mount(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><WorkspaceCodingDefaultsPanel workspaceId="workspace" canManage={canManage} /></QueryClientProvider>);
}
it("selects a team and saves with the observed revision", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : view)); vi.stubGlobal("fetch", fetch); mount();
  await waitFor(() => expect(screen.getByLabelText("Команда coding-агентов")).toHaveTextContent("dbt team"));
  expect(screen.getByRole("list", { name: "Участники команды" })).toHaveTextContent("Data engineer");
  fireEvent.change(screen.getByLabelText("Проект GitLab"), { target: { value: "group/new-project" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки coding" }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
  const mutation = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(mutation[1].body)).toMatchObject({ expected_revision: 7, resources: { project_id: "group/new-project", coding_team_id: "team" } });
});
it("explains why a team is unavailable and blocks saving it", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : view)); vi.stubGlobal("fetch", fetch); mount();
  await waitFor(() => expect(screen.getByLabelText("Команда coding-агентов")).toHaveTextContent("dbt team"));
  await selectOption("Команда coding-агентов", "Old team — недоступна");
  expect(await screen.findByText(/Координатору не разрешён вызов выбранных специалистов: Reviewer/, { selector: "p" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить настройки coding" })).toBeDisabled();
});
it("shows shared settings read-only for a participant", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : view)); vi.stubGlobal("fetch", fetch); mount(false);
  await waitFor(() => expect(screen.getByLabelText("Команда coding-агентов")).toHaveTextContent("dbt team"));
  expect(screen.getByLabelText("Команда coding-агентов")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Сохранить настройки coding" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});
