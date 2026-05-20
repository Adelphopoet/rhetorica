import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "./providers";

const startStreamingMock = vi.fn();
const cancelStreamingMock = vi.fn();
const clearStreamingMock = vi.fn();
const fetchMock = vi.fn();

vi.mock("./result-surface", () => ({
  ResultSurface: ({ isOpen }: { isOpen: boolean }) => (
    <div>{isOpen ? "Mock result surface open" : "Mock result surface closed"}</div>
  ),
}));

vi.mock("@/features/chat/useChatStreaming", () => ({
  useChatStreaming: () => ({
    streamingMessage: null,
    startStreaming: startStreamingMock,
    cancelStreaming: cancelStreamingMock,
    clearStreaming: clearStreamingMock,
  }),
}));

import { ChatWorkspace } from "./chat-workspace";

describe("ChatWorkspace", () => {
  beforeEach(() => {
    startStreamingMock.mockReset();
    cancelStreamingMock.mockReset();
    clearStreamingMock.mockReset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("submits a question and clears the thread on new analysis", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-1",
            workspace_id: "default",
            title: "Show weekly GMV",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:00:00Z",
            message_count: 0,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      )
    );
    startStreamingMock.mockResolvedValue(undefined);

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{
            login: "analyst",
            displayName: "Ada Lovelace",
            role: "admin",
            authProvider: "password",
          }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.change(screen.getByPlaceholderText(/ask a metric/i), {
      target: { value: "Show weekly GMV" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask rhetorica/i }));

    expect(screen.getByText("Show weekly GMV")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/conversations",
        expect.objectContaining({
          method: "POST",
        })
      );
    });
    expect(startStreamingMock).toHaveBeenCalledWith("Show weekly GMV", "conv-1", undefined, undefined);
    expect(screen.queryByText(/mock result surface/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));

    await waitFor(() => {
      expect(screen.queryByText("Show weekly GMV")).not.toBeInTheDocument();
    });
    expect(clearStreamingMock).toHaveBeenCalledTimes(1);
  });

  it("cancels an in-flight request from the composer", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-2",
            workspace_id: "default",
            title: "Why did conversion drop?",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:00:00Z",
            message_count: 0,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      )
    );
    startStreamingMock.mockImplementation(() => new Promise(() => undefined));

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{
            login: "analyst",
            displayName: "Ada Lovelace",
            role: "admin",
            authProvider: "password",
          }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.change(screen.getByPlaceholderText(/ask a metric/i), {
      target: { value: "Why did conversion drop?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /ask rhetorica/i }));

    fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));

    expect(cancelStreamingMock).toHaveBeenCalledTimes(1);
  });

  it("loads a persisted conversation from the sidebar", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-3",
            workspace_id: "default",
            title: "Saved analysis",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:05:00Z",
            messages: [
              {
                id: "msg-user-1",
                conversation_id: "conv-3",
                role: "user",
                content: "Show churn by week",
                created_at: "2026-04-04T10:00:00Z",
                generated_sql: null,
                execution_id: null,
                context_tables: [],
                metadata_sources: [],
                claims: [],
                evidence: [],
                claim_evidence_links: [],
                uncertainty_flags: [],
                selected_connector: null,
                warnings: [],
                feedback: null,
                feedback_updated_at: null,
              },
              {
                id: "msg-assistant-1",
                conversation_id: "conv-3",
                role: "assistant",
                content: "Churn is trending down.",
                created_at: "2026-04-04T10:00:05Z",
                generated_sql: "select 1",
                execution_id: null,
                context_tables: [],
                metadata_sources: [],
                claims: [],
                evidence: [],
                claim_evidence_links: [],
                uncertainty_flags: [],
                selected_connector: "warehouse",
                warnings: [],
                feedback: null,
                feedback_updated_at: null,
              },
            ],
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      )
    );

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{
            login: "analyst",
            displayName: "Ada Lovelace",
            role: "admin",
            authProvider: "password",
          }}
          recentExecutions={[]}
          initialConversations={[
            {
              id: "conv-3",
              workspace_id: "default",
              title: "Saved analysis",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:05:00Z",
              message_count: 2,
            },
          ]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getAllByRole("button", { name: /saved analysis/i })[0]);

    await waitFor(() => {
      expect(screen.getByText("Show churn by week")).toBeInTheDocument();
    });
    expect(screen.getByText("Churn is trending down.")).toBeInTheDocument();
    expect(screen.getByText("Mock result surface closed")).toBeInTheDocument();
  });

  it("rehydrates an active analysis plan when no assistant payload exists yet", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/analysis-run")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              conversation_id: "conv-active",
              status: "running",
              checkpoint_digest: {
                run_id: "run-1",
                plan_id: "plan-1",
                status: "running",
                completed_step_ids: [],
                evidence_ids: [],
                warnings: [],
              },
              analysis_plan: {
                plan_id: "plan-1",
                goal: "Почему упала выручка?",
                status: "running",
                steps: [
                  {
                    id: "baseline",
                    title: "Build SQL-backed baseline",
                    status: "running",
                    required_evidence: ["execution_result"],
                    evidence_ids: [],
                  },
                ],
                required_evidence: ["execution_result"],
                open_questions: [],
                warnings: [],
              },
              worker_results: [],
              resumable: true,
              cancellable: true,
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }
          )
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-active",
            workspace_id: "default",
            title: "Active analysis",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:05:00Z",
            messages: [
              {
                id: "msg-user-active",
                conversation_id: "conv-active",
                role: "user",
                content: "Почему упала выручка?",
                created_at: "2026-04-04T10:00:00Z",
                generated_sql: null,
                execution_id: null,
                context_tables: [],
                metadata_sources: [],
                claims: [],
                evidence: [],
                claim_evidence_links: [],
                uncertainty_flags: [],
                selected_connector: null,
                warnings: [],
                feedback: null,
                feedback_updated_at: null,
              },
            ],
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      );
    });

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{
            login: "analyst",
            displayName: "Ada Lovelace",
            role: "admin",
            authProvider: "password",
          }}
          recentExecutions={[]}
          initialConversations={[
            {
              id: "conv-active",
              workspace_id: "default",
              title: "Active analysis",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:05:00Z",
              message_count: 1,
            },
          ]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getAllByRole("button", { name: /active analysis/i })[0]);

    await waitFor(() => {
      expect(screen.getByText("Build SQL-backed baseline")).toBeInTheDocument();
    });
    expect(screen.getAllByText("running").length).toBeGreaterThan(0);
  });

  it("runs an official scenario through the regular chat flow", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-scn-1",
            workspace_id: "default",
            title: "Prompt 2026-W14",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:00:00Z",
            message_count: 0,
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      )
    );
    startStreamingMock.mockResolvedValue(undefined);

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{
            login: "analyst",
            displayName: "Ada Lovelace",
            role: "admin",
            authProvider: "password",
          }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[
            {
              id: "scn-1",
              slug: "gmv-delta-diagnosis",
              workspace_id: "default",
              owner_user_id: null,
              scope: "official",
              title: "Diagnose GMV delta",
              summary: "Break down GMV movement",
              description: "Official scenario",
              input_schema: {
                type: "object",
                properties: {
                  period_current: { type: "string", title: "Current period" },
                },
                required: ["period_current"],
              },
              prompt_template: "Prompt {{period_current}}",
              tool_policy: {
                allow_run_sql: true,
                sql_guard_required: true,
                allowed_tools: [],
                post_answer_checklist: [],
              },
              default_connector_bindings: {
                llm_connector_name: null,
                metadata_connector_name: null,
                database_connector_name: null,
                run_sql: true,
              },
              status: "published",
              version: 1,
              published_at: "2026-04-04T09:00:00Z",
              created_at: "2026-04-04T09:00:00Z",
              updated_at: "2026-04-04T09:00:00Z",
            },
          ]}
        />
      </Providers>
    );

    expect(screen.queryByPlaceholderText(/Current period/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /browse scenarios/i }));
    fireEvent.change(screen.getByPlaceholderText(/Current period/i), {
      target: { value: "2026-W14" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run scenario/i }));

    expect(screen.getByText("Prompt 2026-W14")).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/chat/conversations",
        expect.objectContaining({
          method: "POST",
        })
      );
    });
    expect(startStreamingMock).toHaveBeenCalledWith("Prompt 2026-W14", "conv-scn-1", undefined, undefined);
  });
});
