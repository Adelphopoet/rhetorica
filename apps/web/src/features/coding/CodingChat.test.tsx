import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CodingChatConfirmation, CodingModeControl, useCodingChat, type CodingChatPlan } from "./CodingChat";
import type { CodingResources } from "./codingApi";

const selection = { root_agent_id: "root", developer_agent_id: "dev", qa_agent_id: "qa", gitlab_connector_id: "gitlab", project_id: "group/project", target_branch: "main", environment_profile_id: "env" };
const resources: CodingResources = { enabled: true, can_start_coding: true, launch_defaults: selection, specialists: ["dev", "qa"].map(id => ({ id, title: id, gitlab_connectors: ["gitlab"], database_connectors: [] })), gitlab_connectors: [{ id: "gitlab", name: "gitlab" }], database_connectors: [], environments: [{ id: "env", revision: 1 }] };
const draft: CodingChatPlan = { id: "plan", conversation_id: "chat", resources: selection, draft: { task: "Add revenue", steps: ["Implement", "QA"], developer_brief: "Develop", qa_brief: "Check", acceptance_criteria: ["Tests pass"] }, run_id: null };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });
function Harness() { const coding = useCodingChat("chat", "root"); return <><CodingModeControl coding={coding} loading={false} /><CodingChatConfirmation coding={coding} busy={false} /></>; }
function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>); }

it("keeps the resource panel open while selecting an option in its portal", async () => {
  localStorage.setItem("rhetorica-coding-mode", "true");
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : url.includes("coding-plans") ? null : [])));
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Настройки кодинга" }));
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Окружение" }), { key: "Enter" });
  const option = await screen.findByRole("option", { name: "Выберите…" });
  fireEvent.pointerDown(option);
  fireEvent.keyDown(option, { key: "Enter" });
  expect(screen.getByRole("dialog", { name: "Ресурсы разработки" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Окружение" })).toHaveTextContent("Выберите…");
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("dialog", { name: "Ресурсы разработки" })).not.toBeInTheDocument();
});

it("toggles coding in composer without a standalone task form or starting work", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : url.includes("coding-plans") ? null : []));
  vi.stubGlobal("fetch", fetch); mount();
  const toggle = await screen.findByRole("button", { name: "Режим кодинга" });
  expect(toggle).toHaveAttribute("aria-pressed", "false"); fireEvent.click(toggle);
  await waitFor(() => expect(screen.queryByText(/Загрузка ресурсов/)).not.toBeInTheDocument());
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByLabelText("Задание для разработки")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Начать разработку" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("restores the persisted confirmation and starts only after an explicit click", async () => {
  localStorage.setItem("rhetorica-coding-mode", "true"); let confirmed = false;
  const fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { confirmed = true; return Response.json({ run_id: "run" }); }
    return Response.json(url.includes("coding-capabilities") ? resources : url.includes("coding-plans") ? { ...draft, run_id: confirmed ? "run" : null } : []);
  }); vi.stubGlobal("fetch", fetch); mount();
  const confirm = await screen.findByRole("button", { name: "Начать разработку" });
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  fireEvent.click(confirm);
  await waitFor(() => expect(confirmed).toBe(true));
  const mutation = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(mutation[0]).toBe("/api/coding/coding-plans/chat/plan/confirm");
  expect(mutation[1].body).toBeUndefined();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Начать разработку" })).not.toBeInTheDocument());
});

it("shows stale-plan denial without retrying or creating another run", async () => {
  localStorage.setItem("rhetorica-coding-mode", "true"); let stale = false;
  const fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { stale = true; return Response.json({ detail: "coding_plan_superseded" }, { status: 409 }); }
    return Response.json(url.includes("coding-capabilities") ? resources : url.includes("coding-plans") ? stale ? null : draft : []);
  }); vi.stubGlobal("fetch", fetch); mount();
  fireEvent.click(await screen.findByRole("button", { name: "Начать разработку" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/План/);
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});


it("hides coding for an agent without rights and does not start work", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? { ...resources, can_start_coding: false, specialists: [], launch_defaults: null } : []));
  vi.stubGlobal("fetch", fetch); mount();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Режим кодинга" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("shows the coding explanation only on hover or focus", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? resources : url.includes("coding-plans") ? null : []))); mount();
  const toggle = await screen.findByRole("button", { name: "Режим кодинга" });
  expect(screen.queryByText(/сначала обсудим/)).not.toBeInTheDocument();
  fireEvent.mouseEnter(toggle.parentElement!);
  expect(screen.getByRole("tooltip")).toHaveTextContent(/после твоего подтверждения/);
  fireEvent.mouseLeave(toggle.parentElement!);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.focus(toggle);
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.blur(toggle);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

it("uses the workspace coordinator when coding is restored before an agent was chosen", async () => {
  localStorage.setItem("rhetorica-coding-mode", "true");
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? { ...resources, root_agent_id: "root" } : [])));
  function NewChat() { const coding = useCodingChat(null, null, "workspace-one"); return <output aria-label="Selected coding resources">{coding.selection.root_agent_id}:{coding.selection.developer_agent_id}:{coding.selection.qa_agent_id}</output>; }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["coding-resources", "workspace-one", null, false], { ...resources, can_start_coding: false, root_agent_id: null, launch_defaults: null });
  render(<QueryClientProvider client={client}><NewChat /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("Selected coding resources")).toHaveTextContent("root:dev:qa"));
  expect(client.getQueryData(["coding-resources", "workspace-one", null, true])).toBeDefined();
  expect(client.getQueryData(["coding-resources", "workspace-two", null, true])).toBeUndefined();
});

it("keeps resource edits when the same workspace preset is refreshed", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => Response.json(url.includes("coding-capabilities") ? { ...resources, launch_defaults: { ...selection } } : [])));
  function ResourceEditor() { const coding = useCodingChat("chat", "root"); return <input aria-label="Целевая ветка" value={coding.selection.target_branch} onChange={event => coding.setSelection(previous => ({ ...previous, target_branch: event.target.value }))} />; }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ResourceEditor /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("Целевая ветка")).toHaveValue("main"));
  fireEvent.change(screen.getByLabelText("Целевая ветка"), { target: { value: "candidate-check" } });
  await client.invalidateQueries({ queryKey: ["coding-resources", "default", "root", false] });
  expect(screen.getByLabelText("Целевая ветка")).toHaveValue("candidate-check");
});
