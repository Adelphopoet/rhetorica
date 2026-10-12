import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceAccessPanel } from "./WorkspaceAccessPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WorkspaceAccessPanel", () => {
  it("supports explicit switching and shows default members by name and login", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => new Promise<Response>(() => undefined));
    render(
      <WorkspaceAccessPanel
        activeWorkspaceId="default"
        workspaces={[
          {
            workspace: {
              id: "default",
              name: "Default",
              is_active: true,
              metadata: {},
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            membership: {
              workspace_id: "default",
              user_id: "admin-id",
              role: "owner",
              is_active: true,
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            is_default: true,
          },
          {
            workspace: {
              id: "analytics",
              name: "Analytics",
              is_active: true,
              metadata: {},
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            membership: {
              workspace_id: "analytics",
              user_id: "admin-id",
              role: "owner",
              is_active: true,
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            is_default: false,
          },
        ]}
        initialMemberships={[
          {
            workspace_id: "default",
            user_id: "member-uuid-not-for-display",
            user_login: "anna",
            user_display_name: "Anna Analyst",
            role: "analyst",
            is_active: true,
            created_at: "2026-09-22T08:00:00Z",
            updated_at: "2026-09-22T08:00:00Z",
          },
        ]}
        initialGrants={[]}
        users={[
          {
            id: "member-uuid-not-for-display",
            home_workspace_id: "default",
            login: "anna",
            display_name: "Anna Analyst",
            system_role: "analyst",
            is_active: true,
          },
        ]}
        resources={[]}
        canManageCatalog={false}
        canManageMemberships
        canCreateWorkspace
      />,
    );

    expect(screen.getByRole("button", { name: "Создать пространство" })).toBeInTheDocument();
    expect(screen.getByText("Anna Analyst")).toBeInTheDocument();
    expect(screen.getByText("@anna")).toBeInTheDocument();
    expect(screen.queryByText("member-uuid-not-for-display")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отозвать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Наблюдатель" })).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Switch workspace" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Текущее рабочее пространство"), "analytics");

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/workspaces/analytics/select", {
        method: "POST",
        cache: "no-store",
      }),
    );
  });

  it("shares an existing resource reference without credential fields", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "grant-1",
          resource_kind: "database_connector",
          resource_id: "trino-1",
          owner_workspace_id: "platform",
          recipient_workspace_id: "analytics",
          alias: "warehouse",
          is_active: true,
          created_by: "admin",
          created_at: "2026-09-22T08:00:00Z",
          updated_at: "2026-09-22T08:00:00Z",
        }),
        { status: 201 },
      ),
    );
    render(
      <WorkspaceAccessPanel
        activeWorkspaceId="platform"
        workspaces={[
          {
            workspace: {
              id: "platform",
              name: "Platform",
              is_active: true,
              metadata: {},
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            membership: {
              workspace_id: "platform",
              user_id: "admin",
              role: "owner",
              is_active: true,
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            is_default: false,
          },
          {
            workspace: {
              id: "analytics",
              name: "Analytics",
              is_active: true,
              metadata: {},
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            membership: {
              workspace_id: "analytics",
              user_id: "admin",
              role: "owner",
              is_active: true,
              created_at: "2026-09-22T08:00:00Z",
              updated_at: "2026-09-22T08:00:00Z",
            },
            is_default: false,
          },
        ]}
        initialMemberships={[]}
        initialGrants={[]}
        users={[]}
        resources={[{ id: "trino-1", name: "Trino", kind: "database_connector" }]}
      />,
    );

    expect(screen.queryByLabelText(/password|token|credential/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Псевдоним"), "warehouse");
    await user.click(screen.getByRole("button", { name: "Поделиться ресурсом" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/workspaces/analytics/resource-grants");
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      resource_kind: "database_connector",
      resource_id: "trino-1",
      owner_workspace_id: "platform",
      recipient_workspace_id: "analytics",
      alias: "warehouse",
    });
    expect(JSON.stringify(body)).not.toMatch(/password|token|credential|secret/i);
  });
});
