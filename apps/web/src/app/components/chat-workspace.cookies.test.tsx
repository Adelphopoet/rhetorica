import { act, fireEvent, render, screen } from "@testing-library/react";
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

function renderWorkspace(
  props: {
    initialComposerDock?: "bottom" | "right";
    initialSidebarCollapsed?: boolean;
    initialComposerDockWidth?: number;
    initialComposerDockHeight?: number;
    initialSidebarWidth?: number;
  } = {}
) {
  return render(
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
        {...props}
      />
    </Providers>
  );
}

describe("ChatWorkspace cookie persistence", () => {
  beforeEach(() => {
    startStreamingMock.mockReset();
    cancelStreamingMock.mockReset();
    clearStreamingMock.mockReset();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    document.cookie =
      "rhetorica_composer_dock=; rhetorica_result_panel_open=; rhetorica_sidebar_collapsed=; rhetorica_composer_dock_width=; rhetorica_composer_dock_height=; rhetorica_sidebar_width=; Max-Age=0";
  });

  it("writes default ui cookies after mount", () => {
    renderWorkspace();
    expect(document.cookie).toContain("rhetorica_composer_dock=bottom");
    expect(document.cookie).toContain("rhetorica_result_panel_open=0");
    expect(document.cookie).toContain("rhetorica_sidebar_collapsed=0");
    expect(document.cookie).toContain("rhetorica_composer_dock_width=380");
    expect(document.cookie).toContain("rhetorica_composer_dock_height=170");
    expect(document.cookie).toContain("rhetorica_sidebar_width=280");
  });

  it("updates the composer dock cookie when the dock toggle is clicked", () => {
    renderWorkspace();
    fireEvent.click(screen.getAllByRole("button", { name: /Разместить справа/i })[0]);
    expect(document.cookie).toContain("rhetorica_composer_dock=right");
  });

  it("updates the sidebar collapsed cookie when the collapse button is clicked", () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: "Свернуть сайдбар" }));
    expect(document.cookie).toContain("rhetorica_sidebar_collapsed=1");
  });

  it("persists composer dock width resizing", () => {
    renderWorkspace({ initialComposerDock: "right" });

    const pointerDown = new Event("pointerdown", { bubbles: true });
    Object.assign(pointerDown, { clientX: 400 });
    act(() => {
      screen.getByRole("separator", { name: "Изменить ширину поля ввода" }).dispatchEvent(pointerDown);
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 360 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });

    expect(document.cookie).toContain("rhetorica_composer_dock_width=420");
  });

  it("persists composer dock height resizing", () => {
    renderWorkspace();

    const pointerDown = new Event("pointerdown", { bubbles: true });
    Object.assign(pointerDown, { clientY: 300 });
    act(() => {
      screen.getByRole("separator", { name: /высоту/i }).dispatchEvent(pointerDown);
      window.dispatchEvent(new MouseEvent("pointermove", { clientY: 250 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });

    expect(document.cookie).toContain("rhetorica_composer_dock_height=220");
  });

  it("persists sidebar width resizing", () => {
    renderWorkspace({ initialSidebarCollapsed: false });

    const pointerDown = new Event("pointerdown", { bubbles: true });
    Object.assign(pointerDown, { clientX: 300 });
    act(() => {
      screen.getByRole("separator", { name: /сайдбара/i }).dispatchEvent(pointerDown);
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 340 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });

    expect(document.cookie).toContain("rhetorica_sidebar_width=320");
  });
});