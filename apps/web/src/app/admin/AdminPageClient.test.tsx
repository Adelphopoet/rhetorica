import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@rhetorica/ui";

import { AdminPageClient } from "./AdminPageClient";

vi.mock("../components/scenario-editor-modal", () => ({
  ScenarioEditorModal: () => null,
}));

vi.mock("@/features/scenarios/scenariosApi", () => ({
  archiveScenario: vi.fn(),
  createScenario: vi.fn(),
  updateScenario: vi.fn(),
}));

function renderAdminPage() {
  return render(
    <ToastProvider>
      <AdminPageClient
        databaseConnectors={[]}
        llmConnectors={[]}
        openMetadataConnectors={[]}
        confluenceConnectors={[]}
        jiraConnectors={[]}
        mattermostConnectors={[]}
        pendingInsights={[
          {
            id: "review-1",
            workspace_id: "default",
            conversation_id: "conv-1",
            message_id: "msg-1",
            execution_id: "exec-1",
            summary: "Check suspicious revenue spike",
            article_markdown: "# Draft",
            answer_excerpt: "Revenue jumped 17% WoW.",
            generated_sql: "select 1",
            context_tables: ["dm_revenue_daily"],
            metadata_sources: ["openmetadata"],
            keywords: ["revenue", "wow"],
            llm_confidence: 0.92,
            llm_reason: "Strong metadata and SQL evidence",
            likes_count: 3,
            verification_status: "pending",
            human_verified: false,
            verified_by: null,
            verified_at: null,
            created_at: "2026-04-05T12:00:00Z",
            updated_at: "2026-04-05T12:00:00Z",
          },
        ]}
        scenarios={[]}
        workspaceSkills={[]}
        localUsers={[
          {
            id: "user-1",
            workspace_id: "default",
            login: "admin",
            role: "admin",
            is_active: true,
            auth_provider: "local",
            auth_subject: "local-user:1",
            display_name: "Admin",
            created_at: "2026-04-05T11:00:00Z",
            updated_at: "2026-04-05T11:00:00Z",
          },
          {
            id: "user-2",
            workspace_id: "default",
            login: "victim",
            role: "analyst",
            is_active: true,
            auth_provider: "local",
            auth_subject: "local-user:2",
            display_name: "Victim",
            created_at: "2026-04-05T12:00:00Z",
            updated_at: "2026-04-05T12:00:00Z",
          },
        ]}
        currentUserId="user-1"
        workspaceId="default"
      />
    </ToastProvider>
  );
}

describe("AdminPageClient", () => {
  it("renders overview readiness and blockers", () => {
    renderAdminPage();

    expect(screen.getByText("Readiness")).toBeInTheDocument();
    expect(screen.getByText("No active database connector configured")).toBeInTheDocument();
    expect(screen.getByText("Clear the review queue")).toBeInTheDocument();
  });

  it("creates a local user from the access section", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "user-3",
          workspace_id: "default",
          login: "new-analyst",
          role: "analyst",
          is_active: true,
          auth_provider: "local",
          auth_subject: "local-user:3",
          display_name: "New Analyst",
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Access/ }));
    await user.type(await screen.findByLabelText("Login"), "new-analyst");
    await user.type(screen.getByLabelText("Password"), "strong-pass-123");
    await user.selectOptions(screen.getByLabelText("Role"), "analyst");
    await user.type(screen.getByLabelText("Display name"), "New Analyst");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/users",
        expect.objectContaining({
          method: "POST",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      login: "new-analyst",
      password: "strong-pass-123",
      role: "analyst",
      display_name: "New Analyst",
    });

    await waitFor(() => {
      expect(screen.getByText("new-analyst")).toBeInTheDocument();
    });
  });

  it("deletes a local user through confirm dialog", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Access/ }));
    await user.click((await screen.findAllByRole("button", { name: "Delete" }))[1]!);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/users/user-2",
        expect.objectContaining({ method: "DELETE", cache: "no-store" })
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("Victim")).not.toBeInTheDocument();
    });
  });

  it("reviews and removes a pending insight optimistically", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Review/ }));
    await user.click(await screen.findByRole("button", { name: "Verify" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/rag-insights/review-1/approve",
        expect.objectContaining({ method: "POST", cache: "no-store" })
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Queue is empty")).toBeInTheDocument();
    });
  });

  it("keeps queue panel copy inside the card bounds", async () => {
    const user = userEvent.setup();

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Review/ }));

    const queueTitle = await screen.findByText("Queue");
    const queueDescription = screen.getByText("1 pending items");

    expect(queueTitle).toHaveClass("break-words");
    expect(queueDescription).toHaveClass("break-words");
  });

  it("creates a jira connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "jira-1",
          name: "jira-main",
          workspace_id: "default",
          is_active: true,
          connection: {
            provider: "jira",
            base_url: "https://jira.company.com",
            username: "jira-user",
            api_token: null,
            auth_type: "basic",
            verify_tls: true,
            project_keys: ["DM", "OPS"],
            issue_limit: 15,
            extra_settings: {},
          },
          has_api_token: true,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Setup/ }));
    await user.click((await screen.findAllByRole("button", { name: "Add Jira" }))[0]!);

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "jira-main");
    await user.type(within(dialog).getByLabelText("Base URL"), "https://jira.company.com");
    await user.type(within(dialog).getByLabelText("Username"), "jira-user");
    await user.type(within(dialog).getByLabelText("API token"), "secret-token");
    await user.selectOptions(within(dialog).getByLabelText("Auth type"), "basic");
    await user.type(within(dialog).getByLabelText("Default projects"), "DM, OPS");
    await user.clear(within(dialog).getByLabelText("Issue limit"));
    await user.type(within(dialog).getByLabelText("Issue limit"), "15");
    await user.click(within(dialog).getByRole("button", { name: "Create connector" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/jira-connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "jira-main",
      workspace_id: "default",
      is_active: true,
      connection: {
        provider: "jira",
        base_url: "https://jira.company.com",
        username: "jira-user",
        api_token: "secret-token",
        auth_type: "basic",
        verify_tls: true,
        project_keys: ["DM", "OPS"],
        issue_limit: 15,
        extra_settings: {},
      },
    });

    await waitFor(() => {
      expect(screen.getByText("jira-main")).toBeInTheDocument();
    });
  });

  it("creates an impala database connector from setup", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "impala-1",
          name: "impala-prod",
          workspace_id: "default",
          is_active: true,
          connection: {
            engine: "impala",
            host: "10.234.176.50",
            port: 21050,
            username: "analyst_user",
            password: null,
            database: null,
            catalog: null,
            schema: "default",
            secure: true,
            verify: false,
            extra_settings: {
              auth_type: "LDAP",
              retries: 1,
              metadata_service: "impala-prod",
              metadata_database: "default",
            },
          },
          has_password: true,
          created_at: "2026-04-05T13:00:00Z",
          updated_at: "2026-04-05T13:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchSpy);

    renderAdminPage();

    await user.click(screen.getByRole("button", { name: /Setup/ }));
    await user.click(await screen.findByRole("button", { name: "Add database" }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "impala-prod");
    await user.selectOptions(within(dialog).getByLabelText("Engine"), "impala");
    await user.type(within(dialog).getByLabelText("Host"), "10.234.176.50");
    await user.clear(within(dialog).getByLabelText("Port"));
    await user.type(within(dialog).getByLabelText("Port"), "21050");
    await user.type(within(dialog).getByLabelText("Username"), "analyst_user");
    await user.type(within(dialog).getByLabelText("Password"), "secret");
    await user.type(within(dialog).getByLabelText("Schema"), "default");
    await user.type(within(dialog).getByLabelText("OpenMetadata service"), "impala-prod");
    await user.type(within(dialog).getByLabelText("OpenMetadata database"), "default");
    await user.click(within(dialog).getByLabelText("Use TLS"));
    await user.click(within(dialog).getByLabelText("Verify TLS certificate"));
    await user.click(within(dialog).getByRole("button", { name: "Create connector" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/connectors",
        expect.objectContaining({
          method: "POST",
          cache: "no-store",
        })
      );
    });

    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      name: "impala-prod",
      workspace_id: "default",
      is_active: true,
      connection: {
        engine: "impala",
        host: "10.234.176.50",
        port: 21050,
        username: "analyst_user",
        password: "secret",
        database: null,
        catalog: null,
        schema: "default",
        secure: true,
        verify: false,
        extra_settings: {
          metadata_service: "impala-prod",
          metadata_database: "default",
          auth_type: "LDAP",
          retries: 1,
        },
      },
    });

    await waitFor(() => {
      expect(screen.getByText("impala-prod")).toBeInTheDocument();
    });
  });
});
