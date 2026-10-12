import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentMenuStore } from "@/features/agents/agentMenuStore";
import { useGuidesPanelStore } from "@/features/guides/guidesPanelStore";
import { ChatComposer } from "./chat-composer";
import { ChatEmptyState } from "./chat-empty-state";

function renderComposerPair() {
  return render(
    <>
      <ChatEmptyState chatWidth={1100} onOpenScenarioCatalog={() => undefined} />
      <ChatComposer
        value=""
        loading={false}
        chatWidth={1100}
        dock="bottom"
        onToggleDock={vi.fn()}
        dockWidth={380}
        dockHeight={170}
        onResizeDockWidth={vi.fn()}
        onResizeDockHeight={vi.fn()}
        onChange={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />
    </>
  );
}

describe("ChatEmptyState", () => {
  beforeEach(() => {
    useAgentMenuStore.setState({ agentMenuOpen: false, agentMenuPulse: false });
    useGuidesPanelStore.setState({ guidesPanelOpen: false });
  });

  it("renders the scenario and agent sections even without agents", () => {
    render(<ChatEmptyState chatWidth={1100} onOpenScenarioCatalog={() => undefined} />);

    expect(screen.getByRole("button", { name: /Сценарии/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Выбрать агента/i })).toBeInTheDocument();
  });

  it("opens the guides panel from the welcome screen", () => {
    render(<ChatEmptyState chatWidth={1100} onOpenScenarioCatalog={() => undefined} />);

    expect(
      screen.getByText(
        "Найдите полезную информацию для решения задач"
      )
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Показать/i }));

    expect(useGuidesPanelStore.getState().guidesPanelOpen).toBe(true);
  });

  it("opens the agent menu of the composer", () => {
    renderComposerPair();

    fireEvent.click(screen.getByRole("button", { name: /Выбрать агента/i }));

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("menu")).toHaveClass("glow-pulse");
  });

  it("closes the agent menu when the button is clicked again", () => {
    renderComposerPair();
    const button = screen.getByRole("button", { name: /Выбрать агента/i });

    fireEvent.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.click(button);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});