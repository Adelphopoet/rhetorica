import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "./providers";
import { STALE_DETECTED_KEY } from "@/lib/staleAssetReload";
import { PENDING_CHAT_ACTION_KEY } from "@/lib/staleRecovery";
import { writeChatDraft } from "@/features/chat/chatDraft";

const startStreamingMock = vi.fn();
const cancelStreamingMock = vi.fn();
const clearStreamingMock = vi.fn();
const fetchMock = vi.fn();
const routerPushMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: routerPushMock, replace: vi.fn(), refresh: vi.fn() }),
}));

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
    routerPushMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("constrains the workspace to the dynamic viewport so the bottom composer stays visible", () => {
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

    expect(screen.getByTestId("chat-workspace")).toHaveClass("h-dvh", "min-h-0");
    expect(screen.getByTestId("chat-workspace-grid")).toHaveClass(
      "h-full",
      "min-h-0",
      "grid-rows-[minmax(0,1fr)]"
    );
    expect(screen.getByTestId("chat-workspace-content")).toHaveClass("min-h-0");
    expect(screen.getByPlaceholderText(/Задайте вопрос о метрике/i)).toBeInTheDocument();
  });

  it("restores a saved new-chat draft into the composer on mount", () => {
    writeChatDraft(window.localStorage, "default", null, "Черновик нового чата");

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

    expect(screen.getByPlaceholderText(/Задайте вопрос о метрике/i)).toHaveValue(
      "Черновик нового чата"
    );
  });

  it("removes a successfully deleted conversation from user history", async () => {
    const conversation = {
      id: "conv-delete",
      workspace_id: "default",
      title: "Delete this chat",
      created_at: "2026-04-04T10:00:00Z",
      updated_at: "2026-04-04T10:00:00Z",
      message_count: 1,
    };
    let deleted = false;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/chat/conversations/conv-delete" && init?.method === "DELETE") {
        deleted = true;
        return Promise.resolve(
          new Response(JSON.stringify({ status: "deleted", conversation_id: conversation.id }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        );
      }
      if (url.startsWith("/api/chat/conversations?")) {
        return Promise.resolve(
          new Response(JSON.stringify(deleted ? [] : [conversation]), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
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
          initialConversations={[conversation]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getByRole("button", { name: "Действия для Delete this chat" }));
    fireEvent.click(await screen.findByRole("button", { name: "Удалить Delete this chat" }));

    await waitFor(() => {
      expect(screen.queryByText("Delete this chat")).not.toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/conversations/conv-delete",
      expect.objectContaining({ method: "DELETE" })
    );
  });

  it("submits a question and clears the thread on new analysis", async () => {
    let createdConversationCount = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/chat/conversations" && init?.method === "POST") {
        createdConversationCount += 1;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: `conv-${createdConversationCount}`,
              workspace_id: "default",
              title: createdConversationCount === 1 ? "Show weekly GMV" : "Show weekly orders",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:00:00Z",
              message_count: 0,
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }
          )
        );
      }
      if (url.startsWith("/api/chat/conversations?")) {
        return Promise.resolve(
          new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
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

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Show weekly GMV" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

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

    fireEvent.click(screen.getByRole("button", { name: /новый чат/i }));

    await waitFor(() => {
      expect(screen.queryByText("Show weekly GMV")).not.toBeInTheDocument();
    });
    expect(clearStreamingMock).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Show weekly orders" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

    await waitFor(() => {
      expect(startStreamingMock).toHaveBeenLastCalledWith(
        "Show weekly orders",
        "conv-2",
        undefined,
        undefined
      );
    });
    expect(createdConversationCount).toBe(2);
  });

  it("does not restore a stale conversation when creation finishes after new chat", async () => {
    let resolveStaleCreation: ((response: Response) => void) | undefined;
    const staleCreation = new Promise<Response>((resolve) => {
      resolveStaleCreation = resolve;
    });
    let createRequestCount = 0;

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/chat/conversations" && init?.method === "POST") {
        createRequestCount += 1;
        if (createRequestCount === 1) {
          return staleCreation;
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: "conv-fresh",
              workspace_id: "default",
              title: "Fresh question",
              created_at: "2026-08-06T07:00:00Z",
              updated_at: "2026-08-06T07:00:00Z",
              message_count: 0,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      if (url.startsWith("/api/chat/conversations?")) {
        return Promise.resolve(
          new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
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

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Stale question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));
    await waitFor(() => expect(createRequestCount).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: /новый чат/i }));
    expect(screen.queryByText("Stale question")).not.toBeInTheDocument();

    await act(async () => {
      resolveStaleCreation?.(
        new Response(
          JSON.stringify({
            id: "conv-stale",
            workspace_id: "default",
            title: "Stale question",
            created_at: "2026-08-06T06:59:00Z",
            updated_at: "2026-08-06T06:59:00Z",
            message_count: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
      await staleCreation;
    });
    expect(startStreamingMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Fresh question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

    await waitFor(() => {
      expect(startStreamingMock).toHaveBeenCalledWith(
        "Fresh question",
        "conv-fresh",
        undefined,
        undefined
      );
    });
    expect(startStreamingMock).toHaveBeenCalledTimes(1);
  });

  it("discards a completed upload when the user switches threads", async () => {
    let resolveUpload: ((response: Response) => void) | undefined;
    const delayedUpload = new Promise<Response>((resolve) => {
      resolveUpload = resolve;
    });
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/chat/conversations" && init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({
          id: "conv-upload",
          workspace_id: "default",
          title: "Analyze data.csv",
          created_at: "2026-08-10T18:00:00Z",
          updated_at: "2026-08-10T18:00:00Z",
          message_count: 0,
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (url === "/api/files" && init?.method === "POST") {
        return delayedUpload;
      }
      if (url === "/api/files/artifact-stale" && init?.method === "DELETE") {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (url.startsWith("/api/chat/conversations?")) {
        return Promise.resolve(
          new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
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
          initialConversations={[]}
          initialScenarios={[]}
        />
      </Providers>
    );

    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).toBeInstanceOf(HTMLInputElement);
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["id\n1\n"], "data.csv", { type: "text/csv" })] },
    });
    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Analyze this file" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/files",
        expect.objectContaining({ method: "POST" })
      );
    });
    expect(screen.queryByText(/работа всё ещё выполняется/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /новый чат/i }));

    await act(async () => {
      resolveUpload?.(new Response(JSON.stringify({
        id: "artifact-stale",
        filename: "data.csv",
        media_type: "text/csv",
        size_bytes: 5,
        kind: "user_upload",
        sha256: "a".repeat(64),
      }), { status: 201, headers: { "Content-Type": "application/json" } }));
      await delayedUpload;
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/files/artifact-stale",
        expect.objectContaining({ method: "DELETE" })
      );
    });
    expect(startStreamingMock).not.toHaveBeenCalled();
    expect(screen.queryByText("data.csv")).not.toBeInTheDocument();
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

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Why did conversion drop?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

    fireEvent.click(await screen.findByRole("button", { name: "Отмена" }));

    expect(cancelStreamingMock).toHaveBeenCalledTimes(1);
  });

  it("switches to another saved chat while an answer is still running", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/chat/conversations" && init?.method === "POST") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: "conv-running",
              workspace_id: "default",
              title: "Running analysis",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:00:00Z",
              message_count: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      if (url === "/api/chat/conversations/conv-target") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: "conv-target",
              workspace_id: "default",
              title: "Target chat",
              agent_id: null,
              created_at: "2026-04-03T10:00:00Z",
              updated_at: "2026-04-03T10:05:00Z",
              messages: [
                {
                  id: "target-message",
                  conversation_id: "conv-target",
                  role: "assistant",
                  content: "Loaded target answer",
                  created_at: "2026-04-03T10:05:00Z",
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
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      if (url.startsWith("/api/chat/conversations?")) {
        return Promise.resolve(
          new Response(
            JSON.stringify([
              {
                id: "conv-running",
                workspace_id: "default",
                title: "Running analysis",
                created_at: "2026-04-04T10:00:00Z",
                updated_at: "2026-04-04T10:00:00Z",
                message_count: 1,
              },
              {
                id: "conv-target",
                workspace_id: "default",
                title: "Target chat",
                created_at: "2026-04-03T10:00:00Z",
                updated_at: "2026-04-03T10:05:00Z",
                message_count: 1,
              },
            ]),
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
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
          initialConversations={[
            {
              id: "conv-target",
              workspace_id: "default",
              title: "Target chat",
              created_at: "2026-04-03T10:00:00Z",
              updated_at: "2026-04-03T10:05:00Z",
              message_count: 1,
            },
          ]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.change(screen.getByPlaceholderText(/Задайте вопрос о метрике/i), {
      target: { value: "Keep working in the background" },
    });
    fireEvent.click(screen.getByRole("button", { name: /отправить вопрос/i }));

    await waitFor(() => {
      expect(startStreamingMock).toHaveBeenCalledWith(
        "Keep working in the background",
        "conv-running",
        undefined,
        undefined
      );
    });

    const targetButton = screen.getByTitle("Target chat");
    fireEvent.click(targetButton);

    await waitFor(() => {
      expect(screen.getByText("Loaded target answer")).toBeInTheDocument();
    });
    expect(clearStreamingMock).toHaveBeenCalled();
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
                content: "Churn is trending down",
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

    fireEvent.click(screen.getByTitle("Saved analysis"));

    await waitFor(() => {
      expect(screen.getByText("Show churn by week")).toBeInTheDocument();
    });
    expect(screen.getByText("Churn is trending down")).toBeInTheDocument();
    expect(screen.queryByText("Mock result surface open")).not.toBeInTheDocument();
  });

  it("keeps the thread placeholder visible instead of the new chat greeting while a saved chat loads", async () => {
    let releaseConversationDetail: (() => void) | null = null;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/chat/conversations/conv-saved") {
        return new Promise<Response>((resolve) => {
          releaseConversationDetail = () => {
            resolve(
              new Response(
                JSON.stringify({
                  id: "conv-saved",
                  workspace_id: "default",
                  title: "Saved analysis",
                  created_at: "2026-04-04T10:00:00Z",
                  updated_at: "2026-04-04T10:05:00Z",
                  messages: [
                    {
                      id: "msg-user-saved",
                      conversation_id: "conv-saved",
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
                  ],
                }),
                { status: 200, headers: { "Content-Type": "application/json" } }
              )
            );
          };
        });
      }
      return Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
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
              id: "conv-saved",
              workspace_id: "default",
              title: "Saved analysis",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:05:00Z",
              message_count: 1,
            },
          ]}
          initialScenarios={[]}
        />
      </Providers>
    );

    expect(screen.getByTestId("chat-empty-state-card")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Saved analysis"));

    await waitFor(() => {
      expect(screen.getByTestId("chat-thread-skeleton")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("chat-empty-state-card")).not.toBeInTheDocument();

    await act(async () => {
      releaseConversationDetail?.();
    });

    await waitFor(() => {
      expect(screen.getByText("Show churn by week")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("chat-thread-skeleton")).not.toBeInTheDocument();
  });

  it("shows a detached running placeholder when the saved thread ends with a user turn", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      void input;
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: "conv-detached",
            workspace_id: "default",
            title: "Detached run",
            created_at: "2026-04-04T10:00:00Z",
            updated_at: "2026-04-04T10:05:00Z",
            messages: [
              {
                id: "msg-user-detached",
                conversation_id: "conv-detached",
                role: "user",
                content: "пример строк из art_price",
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
              id: "conv-detached",
              workspace_id: "default",
              title: "Detached run",
              created_at: "2026-04-04T10:00:00Z",
              updated_at: "2026-04-04T10:05:00Z",
              message_count: 1,
            },
          ]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getByTitle("Detached run"));

    await waitFor(() => {
      expect(screen.getByText("пример строк из art_price")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/работа всё ещё выполняется/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/ответ формируется/i)).toBeInTheDocument();
  });

  it.each([true, false])("runs a legacy scenario through the API with workflow flag %s", async (enabled) => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const isStart = String(input).includes("/scenarios/scn-1/runs");
      const isLegacy = String(input) === "/api/scenarios/scn-1/run";
      const payload = isStart ? (enabled ? { run_id: "run-1", run: { id: "run-1", conversation_id: "conv-scn-1", status: "queued" } } : { code: "scenario_workflows_disabled", detail: "Workflow runs disabled" }) : isLegacy ? { conversation_id: null } : [];
      return Promise.resolve(new Response(JSON.stringify(payload), { status: isStart ? (enabled ? 202 : 503) : 200, headers: { "Content-Type": "application/json" } }));
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

    fireEvent.click(screen.getByRole("button", { name: /сценарии/i }));
    fireEvent.change(screen.getByPlaceholderText(/Current period/i), {
      target: { value: "2026-W14" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Запустить сценарий" }));

    expect(screen.queryByText("Prompt 2026-W14")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/scenarios/scn-1/runs",
        expect.objectContaining({ method: "POST", headers: expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
          body: JSON.stringify({ workspace_id: "default", inputs: { period_current: "2026-W14" }, conversation_id: null }) })
      );
    });
    if (!enabled) await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/scenarios/scn-1/run", expect.objectContaining({ method: "POST" })));
    expect(startStreamingMock).not.toHaveBeenCalled();
  });

it("keeps the New button waiting while scenario capabilities load and opens the editor afterwards", async () => {
    let resolveCapabilities!: (value: Response) => void;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === "/api/workflows/scenario-capabilities?workspace_id=default") {
        return new Promise<Response>((resolve) => {
          resolveCapabilities = resolve;
        });
      }
      throw new Error(`Unexpected fetch: ${String(input)}`);
    });

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{ login: "analyst", displayName: "Ada Lovelace", role: "admin", authProvider: "password" }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getByRole("button", { name: /сценарии/i }));
    fireEvent.click(screen.getByRole("button", { name: "Мои сценарии" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    const pendingButton = await screen.findByRole("button", { name: "Открываю редактор…" });
    expect(pendingButton).toBeDisabled();
    expect(pendingButton).toHaveTextContent("Открываю редактор…");
    expect(screen.getByRole("heading", { name: "Сценарии" })).toBeInTheDocument();

    act(() => {
      resolveCapabilities(
        new Response(
          JSON.stringify({ enabled: false, agents: [], database_connectors: [], tools: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    });

    expect(await screen.findByRole("heading", { name: "Создание сценария" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Сценарии" })).not.toBeInTheDocument();
  });

  it("reports a failed capabilities request and restores the New button", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === "/api/workflows/scenario-capabilities?workspace_id=default") {
        return Promise.resolve(new Response("boom", { status: 500 }));
      }
      throw new Error(`Unexpected fetch: ${String(input)}`);
    });

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{ login: "analyst", displayName: "Ada Lovelace", role: "admin", authProvider: "password" }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[]}
        />
      </Providers>
    );

    fireEvent.click(screen.getByRole("button", { name: /сценарии/i }));
    fireEvent.click(screen.getByRole("button", { name: "Мои сценарии" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Редактор сценариев недоступен")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Сценарии" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Создать" })).toBeEnabled());
  });

  it("shows a wait indicator while navigating to the workflow launch page", async () => {
    const workflowScenario = {
      id: "scn-workflow",
      slug: "gmv-chain",
      workspace_id: "default",
      owner_user_id: null,
      scope: "official" as const,
      title: "Цепочка GMV",
      summary: "Разбор GMV",
      description: "Официальная цепочка",
      input_schema: { type: "object", properties: {}, required: [] },
      prompt_template: "",
      tool_policy: { allow_run_sql: true, sql_guard_required: true, allowed_tools: [], post_answer_checklist: [] },
      default_connector_bindings: { llm_connector_name: null, database_connector_name: null, run_sql: true },
      status: "published" as const,
      version: 1,
      published_at: null,
      created_at: "",
      updated_at: "",
      execution_kind: "workflow" as const,
    };

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      throw new Error(`Unexpected fetch: ${String(input)}`);
    });

    render(
      <Providers>
        <ChatWorkspace
          workspaceName="default"
          canAccessAdmin
          apiStatus="ok (dev)"
          currentUser={{ login: "analyst", displayName: "Ada Lovelace", role: "admin", authProvider: "password" }}
          recentExecutions={[]}
          initialConversations={[]}
          initialScenarios={[workflowScenario]}
        />
      </Providers>
    );

    fireEvent.click(screen.getByRole("button", { name: /сценарии/i }));
    fireEvent.click(screen.getByRole("button", { name: /К запуску Цепочка GMV/ }));

    await waitFor(() => expect(routerPushMock).toHaveBeenCalledWith("/scenarios/scn-workflow/run"));
    const launchButton = screen.getByRole("button", { name: /К запуску Цепочка GMV/ });
    expect(launchButton).toBeDisabled();
    expect(launchButton).toHaveTextContent("Открываю запуск…");
    expect(screen.getByRole("heading", { name: "Сценарии" })).toBeInTheDocument();
  });

  describe("recovery after a stale asset reload", () => {
    const pendingSend = {
      type: "send",
      workspaceId: "default",
      conversationId: null,
      draftText: "Сохранённый вопрос",
      hadAttachments: false,
    };
    const pendingSwitch = {
      type: "switch",
      workspaceId: "default",
      targetConversationId: "conv-target",
      draftText: "черновик целевого",
      hadAttachments: false,
    };

    beforeEach(() => {
      window.sessionStorage.setItem(STALE_DETECTED_KEY, String(Date.now()));
    });

    function renderWorkspace() {
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
                id: "conv-target",
                workspace_id: "default",
                title: "Target chat",
                created_at: "2026-04-03T10:00:00Z",
                updated_at: "2026-04-03T10:05:00Z",
                message_count: 1,
              },
            ]}
            initialScenarios={[]}
          />
        </Providers>
      );
    }

    it("restores the draft into the composer for a pending send without auto-resending", async () => {
      window.sessionStorage.setItem(PENDING_CHAT_ACTION_KEY, JSON.stringify(pendingSend));
      fetchMock.mockImplementation((input: RequestInfo | URL) => {
        if (String(input).startsWith("/api/chat/conversations?")) {
          return Promise.resolve(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }));
        }
        throw new Error(`Unexpected fetch: ${input}`);
      });

      renderWorkspace();

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/Задайте вопрос о метрике/i)).toHaveValue(
          "Сохранённый вопрос"
        );
      });
      expect(window.sessionStorage.getItem(PENDING_CHAT_ACTION_KEY)).toBeNull();
      expect(startStreamingMock).not.toHaveBeenCalled();
    });

    it("opens the target chat and restores its draft after a pending switch", async () => {
      window.sessionStorage.setItem(PENDING_CHAT_ACTION_KEY, JSON.stringify(pendingSwitch));
      fetchMock.mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/chat/conversations/conv-target") {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                id: "conv-target",
                workspace_id: "default",
                title: "Target chat",
                agent_id: null,
                created_at: "2026-04-03T10:00:00Z",
                updated_at: "2026-04-03T10:05:00Z",
                messages: [
                  {
                    id: "target-message",
                    conversation_id: "conv-target",
                    role: "assistant",
                    content: "Loaded target answer",
                    created_at: "2026-04-03T10:05:00Z",
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
              { status: 200, headers: { "Content-Type": "application/json" } }
            )
          );
        }
        if (url.startsWith("/api/chat/conversations?")) {
          return Promise.resolve(
            new Response(
              JSON.stringify([
                {
                  id: "conv-target",
                  workspace_id: "default",
                  title: "Target chat",
                  created_at: "2026-04-03T10:00:00Z",
                  updated_at: "2026-04-03T10:05:00Z",
                  message_count: 1,
                },
              ]),
              { status: 200, headers: { "Content-Type": "application/json" } }
            )
          );
        }
        throw new Error(`Unexpected fetch: ${url}`);
      });

      renderWorkspace();

      expect(await screen.findByText("Loaded target answer")).toBeInTheDocument();
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/Задайте вопрос о метрике/i)).toHaveValue(
          "черновик целевого"
        );
      });
      expect(window.sessionStorage.getItem(PENDING_CHAT_ACTION_KEY)).toBeNull();
    });

    it("keeps the stale detection marked when the target chat fails to load", async () => {
      window.sessionStorage.setItem(PENDING_CHAT_ACTION_KEY, JSON.stringify(pendingSwitch));
      fetchMock.mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/chat/conversations/conv-target") {
          return Promise.resolve(new Response("boom", { status: 500 }));
        }
        if (url.startsWith("/api/chat/conversations?")) {
          return Promise.resolve(
            new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })
          );
        }
        throw new Error(`Unexpected fetch: ${url}`);
      });

      renderWorkspace();

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/Задайте вопрос о метрике/i)).toHaveValue(
          "черновик целевого"
        );
      });
      expect(await screen.findByText("Не удалось загрузить историю")).toBeInTheDocument();
      expect(window.sessionStorage.getItem(STALE_DETECTED_KEY)).not.toBeNull();
    });
  });
});

  it("opens guides panel from welcome screen and closes result panel when result panel was open in initial state", async () => {
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
          initialResultPanelOpen={false}
        />
      </Providers>
    );

    expect(document.cookie).not.toContain("rhetorica_result_panel_open=1");

    const showGuidesButton = screen.getByRole("button", { name: "Показать" });
    fireEvent.click(showGuidesButton);

    await waitFor(() => {
      expect(screen.getByTestId("guides-surface")).toBeInTheDocument();
    });
    expect(document.cookie).not.toContain("rhetorica_result_panel_open=1");
    expect(screen.queryByText("Mock result surface open")).not.toBeInTheDocument();
  });
