import { selectOption } from "@/test/selectOption";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider } from "@rhetorica/ui";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationSummary } from "@/features/chat/historyApi";
import { Sidebar } from "./sidebar";

function renderSidebar(
  canAccessAdmin: boolean,
  conversations: ConversationSummary[] = [],
  overrides: Partial<{
    collapsed: boolean;
    onCollapseChange: (collapsed: boolean) => void;
    activeConversationId: string | null;
    onSelectConversation: (conversationId: string) => void;
    onNewChat: () => void;
    workspaces: Array<{ id: string; name: string; role: "owner" | "analyst" | "viewer" }>;
  }> = {}
) {
  return render(
    <ToastProvider>
      <Sidebar
        isOpen
        onClose={vi.fn()}
        collapsed={overrides.collapsed ?? false}
        onCollapseChange={overrides.onCollapseChange ?? vi.fn()}
        workspaceName="default"
        workspaces={overrides.workspaces}
        apiStatus="ok (dev)"
        canAccessAdmin={canAccessAdmin}
        userLogin="analyst"
        userDisplayName="Ada Lovelace"
        userRole={canAccessAdmin ? "admin" : "analyst"}
        userAuthProvider="password"
        conversations={conversations}
        activeConversationId={overrides.activeConversationId ?? null}
        historyLoading={false}
        interactionDisabled={false}
        deletionDisabled={false}
        deletingConversationId={null}
        onNewChat={overrides.onNewChat ?? vi.fn()}
        onSelectConversation={overrides.onSelectConversation ?? vi.fn()}
        onDeleteConversation={vi.fn()}
      />
    </ToastProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Sidebar", () => {
  it("does not render logout in the sidebar anymore", () => {
    renderSidebar(true);

    expect(screen.queryByRole("button", { name: /выйти/i })).not.toBeInTheDocument();
  });

  it("renders the brand name and the profile menu trigger in the top row", () => {
    renderSidebar(true);

    expect(screen.getByText("Rhetorica")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /открыть меню профиля/i })).toBeInTheDocument();
  });

  it("does not show the old admin shortcut in the sidebar", () => {
    renderSidebar(false);

    expect(screen.queryByLabelText("Open admin")).not.toBeInTheDocument();
  });

  it("does not render a scenarios button in the sidebar", () => {
    renderSidebar(true);

    expect(screen.queryByRole("button", { name: /scenarios/i })).not.toBeInTheDocument();
  });

  it("renders a deterministic conversation timestamp", () => {
    renderSidebar(true, [
      {
        id: "conversation-1",
        workspace_id: "default",
        title: "Revenue review",
        created_at: "2026-07-23T06:01:00Z",
        updated_at: "2026-07-23T06:01:00Z",
        message_count: 2,
      },
    ], { activeConversationId: "conversation-1" });

    fireEvent.click(screen.getByRole("button", { name: "Действия для Revenue review" }));

    expect(screen.getByText("23 июл., 09:01")).toBeInTheDocument();
  });

  it("opens a saved conversation from the keyboard", async () => {
    const onSelectConversation = vi.fn();
    renderSidebar(true, [
      {
        id: "conversation-1",
        workspace_id: "default",
        title: "Revenue review",
        created_at: "2026-07-23T06:01:00Z",
        updated_at: "2026-07-23T06:01:00Z",
        message_count: 2,
      },
    ], { onSelectConversation });

    const conversation = screen.getByRole("button", { name: "Revenue review" });
    conversation.focus();
    await userEvent.setup().keyboard("{Enter}");

    expect(conversation).toHaveFocus();
    expect(onSelectConversation).toHaveBeenCalledWith("conversation-1");
  });

  it("renders the brand name in the expanded top row", () => {
    renderSidebar(true);

    expect(screen.getByText("Rhetorica")).toBeInTheDocument();
  });

  it("renders the collapse button in the expanded top row", () => {
    const onCollapseChange = vi.fn();
    renderSidebar(true, [], { onCollapseChange });

    screen.getByRole("button", { name: "Свернуть сайдбар" }).click();

    expect(onCollapseChange).toHaveBeenCalledWith(true);
  });

  it("renders the new chat button when no conversation is active", () => {
    const onNewChat = vi.fn();
    renderSidebar(true, [], { onNewChat });

    fireEvent.click(screen.getByRole("button", { name: "Новый чат" }));

    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("renders the collapsed rail with expand, new chat and profile buttons", () => {
    renderSidebar(true, [], { collapsed: true });

    expect(screen.getByRole("button", { name: "Развернуть сайдбар" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Новый чат" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "Открыть профиль" })).toBeInTheDocument();
  });

  it("expands the sidebar and opens the profile menu from the rail avatar", async () => {
    const onCollapseChange = vi.fn();
    renderSidebar(true, [], { collapsed: true, onCollapseChange });

    fireEvent.click(screen.getByRole("button", { name: "Открыть профиль" }));
    fireEvent.click(screen.getByRole("button", { name: "Развернуть сайдбар" }));

    expect(onCollapseChange).toHaveBeenNthCalledWith(1, false);
    expect(onCollapseChange).toHaveBeenNthCalledWith(2, false);
    expect(await screen.findByRole("menu", { name: "Меню профиля" })).toBeInTheDocument();
  });

  it("shows a retryable error when workspace switching fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    vi.stubGlobal("fetch", fetchMock);
    renderSidebar(true, [], {
      workspaces: [
        { id: "default", name: "Default", role: "owner" },
        { id: "analytics", name: "Analytics", role: "analyst" },
      ],
    });

    const select = screen.getByRole("combobox", { name: "Workspace" });
    await selectOption("Workspace", "Analytics");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось переключить workspace. Повторите попытку",
    );
    expect(select).toBeEnabled();
    expect(select).toHaveTextContent("Default");
    expect(fetchMock).toHaveBeenCalledWith("/api/workspaces/analytics/select", { method: "POST" });
  });
});
