import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentProfile } from "@/features/agents/agentsApi";
import type { GitLabConnectorItem } from "./types";
import { GovernedPolicyEditor } from "./GovernedPolicyEditor";

const policy = {
  workspace_id: "default",
  agent_id: "agent-1",
  revision: 1,
  gitlab_write_scopes: [],
  allowed_subagent_ids: [],
  delegation_grants: [],
  max_iterations: 100,
  max_runtime_seconds: 1200,
};

const agent: AgentProfile = {
  id: "agent-1",
  workspace_id: "default",
  slug: "agent",
  title: "Agent",
  description: null,
  is_active: true,
  llm_connector_name: null,
  allowed_database_connector_names: [],
  allowed_rag_connector_names: [],
  allowed_documentation_connector_names: [],
  allowed_gitlab_connector_names: ["gitlab-main"],
  allowed_mcp_connector_names: [],
  allowed_airflow_connector_names: [],
  allowed_kubernetes_connector_names: [],
  allowed_victoriametrics_connector_names: [],
  allowed_workspace_skill_ids: [],
  settings: { run_sql_allowed: true, temperature: null, max_tokens: null },
  star_count: 0,
  is_starred: false,
  is_favorite: false,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
};

function connector(
  id: string,
  name: string,
  overrides: Partial<GitLabConnectorItem> = {},
): GitLabConnectorItem {
  return {
    id,
    name,
    workspace_id: "default",
    is_active: true,
    has_api_token: true,
    connection: {
      provider: "gitlab",
      base_url: "https://gitlab.example.test",
      use_when: "Governed coding",
      verify_tls: true,
      default_project_paths: [],
      default_group_paths: [],
      search_limit: 5,
      read_max_tokens: 1600,
      coding_archive_max_files: 20000,
    },
    created_at: "2026-09-25T00:00:00Z",
    updated_at: "2026-09-25T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GovernedPolicyEditor", () => {
  it("loads and saves a full revisioned replacement independently", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(policy), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...policy, revision: 2, max_iterations: 12 }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent]}
        gitlabConnectors={[]}
      />,
    );

    expect(await screen.findByText(/Версия 1/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Максимум итераций"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));

    await waitFor(() => expect(screen.getByText(/Версия 2/)).toBeInTheDocument());
    const [, request] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(request.method).toBe("PUT");
    expect(JSON.parse(String(request.body))).toMatchObject({
      revision: 1,
      max_iterations: 12,
    });
  });

  it("keeps the draft and reports a stale revision conflict", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(policy), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: { reason_code: "policy_revision_conflict", current_revision: 3 },
          }),
          { status: 409 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...policy, revision: 3, max_iterations: 30 }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent]}
        gitlabConnectors={[]}
      />,
    );

    await screen.findByText(/Версия 1/);
    fireEvent.change(screen.getByLabelText("Максимум итераций"), { target: { value: "17" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));

    expect(await screen.findByText("Политика изменена другим пользователем")).toBeInTheDocument();
    expect(screen.getByDisplayValue("17")).toBeInTheDocument();
    expect(screen.getByText(/Текущая версия: 3/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/Версия 3/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("30")).toBeInTheDocument();
  });

  it("does not create a delegation grant when a child is only allowed", async () => {
    const child = { ...agent, id: "child-1", slug: "child", title: "Child" };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(policy), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ ...policy, revision: 2, allowed_subagent_ids: [child.id] }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent, child]}
        gitlabConnectors={[]}
      />,
    );

    await screen.findByText(/Версия 1/);
    fireEvent.click(screen.getByRole("button", { name: "Child" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));
    await screen.findByText(/Версия 2/);

    const [, request] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(request.body))).toMatchObject({
      allowed_subagent_ids: [child.id],
      delegation_grants: [],
    });
  });

  it("rejects invalid limits locally and loads the default-assistant endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ ...policy, agent_id: undefined }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "default-assistant" }}
        agents={[agent]}
        gitlabConnectors={[]}
      />,
    );

    await screen.findByText(/Версия 1/);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/admin/default-assistant-policy");
    fireEvent.change(screen.getByLabelText("Время выполнения, с"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));

    expect(
      await screen.findByText("Лимиты итераций и времени должны быть целыми положительными числами"),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("filters subagents and connectors using live workspace and agent admission", async () => {
    const eligibleChild = {
      ...agent,
      id: "child-eligible",
      slug: "eligible",
      title: "Eligible child",
    };
    const inactiveChild = {
      ...agent,
      id: "child-inactive",
      slug: "inactive",
      title: "Inactive child",
      is_active: false,
    };
    const foreignChild = {
      ...agent,
      id: "child-foreign",
      slug: "foreign",
      title: "Foreign child",
      workspace_id: "foreign",
    };
    const allowed = connector("connector-allowed", "gitlab-main");
    const notAdmitted = connector("connector-other", "gitlab-other");
    const inactive = connector("connector-inactive", "gitlab-main", { is_active: false });
    const foreign = connector("connector-foreign", "gitlab-main", {
      workspace_id: "foreign",
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify(policy), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent, eligibleChild, inactiveChild, foreignChild]}
        gitlabConnectors={[allowed, notAdmitted, inactive, foreign]}
      />,
    );

    await screen.findByText(/Версия 1/);
    expect(screen.getByRole("button", { name: "Eligible child" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Inactive child" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Foreign child" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Добавить область" }));
    const connectorSelect = screen.getByLabelText("Подключение");
    expect(connectorSelect).toHaveValue("connector-allowed");
    expect(screen.getByRole("option", { name: "gitlab-main" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "gitlab-other" })).not.toBeInTheDocument();
  });

  it("removes inactive and deleted subagent references with their delegated grants", async () => {
    const inactiveChild = {
      ...agent,
      id: "child-inactive",
      slug: "inactive",
      title: "Inactive child",
      is_active: false,
    };
    const deletedChildId = "child-deleted";
    const stalePolicy = {
      ...policy,
      allowed_subagent_ids: [inactiveChild.id, deletedChildId],
      delegation_grants: [
        { subagent_id: inactiveChild.id, gitlab_write_scopes: [] },
        { subagent_id: deletedChildId, gitlab_write_scopes: [] },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(stalePolicy), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...stalePolicy, revision: 2 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent, inactiveChild]}
        gitlabConnectors={[]}
      />,
    );

    expect(await screen.findByText("Недоступные субагенты")).toBeInTheDocument();
    expect(screen.getByText("Недоступен: Inactive child")).toBeInTheDocument();
    expect(screen.getByText(`Недоступен: ${deletedChildId}`)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Удалить недоступного субагента Inactive child" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: `Удалить недоступного субагента ${deletedChildId}` }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));
    await screen.findByText(/Версия 2/);

    const [, request] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(request.body))).toMatchObject({
      allowed_subagent_ids: [],
      delegation_grants: [],
    });
  });

  it("shows a server validation reason without replacing the draft", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(policy), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "gitlab_connector_not_accessible" }), {
          status: 422,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GovernedPolicyEditor
        workspaceId="default"
        target={{ kind: "agent", agent }}
        agents={[agent]}
        gitlabConnectors={[]}
      />,
    );

    await screen.findByText(/Версия 1/);
    fireEvent.change(screen.getByLabelText("Максимум итераций"), { target: { value: "19" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить политику" }));

    expect(await screen.findByText("gitlab_connector_not_accessible")).toBeInTheDocument();
    expect(screen.getByDisplayValue("19")).toBeInTheDocument();
  });
});
