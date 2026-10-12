import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useGuidesPanelStore } from "@/features/guides/guidesPanelStore";
import { ChatTools } from "./chat-tools";
import { startAppTour } from "./app-tour";

vi.mock("./app-tour", () => ({
  startAppTour: vi.fn(),
}));

function renderChatTools(props?: Partial<ComponentProps<typeof ChatTools>>) {
  return render(
    <ToastProvider>
      <ChatTools
        canCopyChatHistory={false}
        chatWidth={1100}
        hasResultPayload
        isResultPanelOpen={false}
        onChangeChatWidth={vi.fn()}
        onCopyChatHistory={vi.fn(async () => undefined)}
        onToggleSidebar={vi.fn()}
        onToggleResultPanel={vi.fn()}
        {...props}
      />
    </ToastProvider>
  );
}

describe("ChatTools", () => {
  beforeEach(() => {
    useGuidesPanelStore.setState({ guidesPanelOpen: false });
  });

  it("shows the result panel button only when the panel is closed", async () => {
    const { rerender } = renderChatTools();

    expect(screen.getByRole("button", { name: /открыть панель результатов/i })).toBeInTheDocument();

    rerender(
      <ToastProvider>
        <ChatTools
          canCopyChatHistory={false}
          chatWidth={1100}
          hasResultPayload
          isResultPanelOpen
          onChangeChatWidth={vi.fn()}
          onCopyChatHistory={vi.fn(async () => undefined)}
          onToggleSidebar={vi.fn()}
          onToggleResultPanel={vi.fn()}
        />
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /открыть панель результатов/i })).not.toBeInTheDocument();
    });
  });

  it("provides a sidebar toggle for mobile layouts", () => {
    const onToggleSidebar = vi.fn();

    renderChatTools({ onToggleSidebar });

    const toggle = screen.getByRole("button", { name: /открыть сайдбар/i });
    expect(toggle).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(onToggleSidebar).toHaveBeenCalledTimes(1);
  });

  it("toggles the composer dock from the mobile rail", () => {
    const onToggleComposerDock = vi.fn();

    renderChatTools({ composerDock: "right", onToggleComposerDock });

    const toggle = screen.getByRole("button", { name: "Разместить внизу" });
    expect(toggle).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(onToggleComposerDock).toHaveBeenCalledTimes(1);
  });

  it("copies the full chat history from the tools rail", async () => {
    const onCopyChatHistory = vi.fn(async () => undefined);

    renderChatTools({
      canCopyChatHistory: true,
      onCopyChatHistory,
    });

    fireEvent.click(screen.getByRole("button", { name: /скопировать чат/i }));

    await waitFor(() => {
      expect(onCopyChatHistory).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("button", { name: /скопировано/i })).toBeInTheDocument();
  });

  it("hides the copy button when there is nothing to copy", () => {
    renderChatTools({ canCopyChatHistory: false });

    expect(screen.queryByRole("button", { name: /скопировать чат/i })).not.toBeInTheDocument();
  });

  it("starts the interface tour from the question icon", () => {
    renderChatTools();

    const tourButton = screen.getByRole("button", { name: "Тур по интерфейсу" });
    expect(tourButton).toBeInTheDocument();

    fireEvent.click(tourButton);
    expect(startAppTour).toHaveBeenCalledTimes(1);
  });

  it("exposes the current theme state and the next action", () => {
    renderChatTools();

    const toggle = screen.getByRole("button", { name: "Тема интерфейса" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAttribute("title", "Включить светлую тему");
  });

  it("opens the guides panel from the rail and closes it on the next click", () => {
    renderChatTools();

    const toggle = screen.getByRole("button", { name: "Полезные инструкции" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(toggle);
    expect(useGuidesPanelStore.getState().guidesPanelOpen).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Полезные инструкции" }));
    expect(useGuidesPanelStore.getState().guidesPanelOpen).toBe(false);
  });

  it("closes the result panel before opening the guides panel", () => {
    const onToggleResultPanel = vi.fn();

    renderChatTools({ isResultPanelOpen: true, onToggleResultPanel });

    fireEvent.click(screen.getByRole("button", { name: "Полезные инструкции" }));

    expect(onToggleResultPanel).not.toHaveBeenCalled();
    expect(useGuidesPanelStore.getState().guidesPanelOpen).toBe(true);
  });

  it("opens the chat width menu and reports the selected width", () => {
    const onChangeChatWidth = vi.fn();

    renderChatTools({ chatWidth: 1100, onChangeChatWidth });

    const toggle = screen.getByRole("button", { name: /ширина чата/i });
    expect(toggle).toBeInTheDocument();

    fireEvent.click(toggle);

    expect(screen.getByRole("menu", { name: /ширина чата/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Обычный/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Расширенный/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Во всю ширину/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: /Расширенный/i }));
    expect(onChangeChatWidth).toHaveBeenCalledWith(1600);
  });

  it("closes the chat width menu with Escape", () => {
    renderChatTools();

    fireEvent.click(screen.getByRole("button", { name: /ширина чата/i }));
    expect(screen.getByRole("menu", { name: /ширина чата/i })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: /ширина чата/i })).not.toBeInTheDocument();
  });

  it("does not render workspace or api status labels in the rail", () => {
    renderChatTools();

    expect(screen.queryByText("default")).not.toBeInTheDocument();
    expect(screen.queryByText(/api/i)).not.toBeInTheDocument();
  });
});
