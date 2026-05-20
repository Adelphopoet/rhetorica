import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { describe, expect, it, vi } from "vitest";

import { Header } from "./header";

function renderHeader(props?: Partial<ComponentProps<typeof Header>>) {
  return render(
    <ToastProvider>
      <Header
        workspaceName="default"
        trustLabel="Visible SQL and governed execution."
        apiStatus="ok (dev)"
        canCopyChatHistory={false}
        hasResultPayload
        isResultPanelOpen={false}
        onCopyChatHistory={vi.fn(async () => undefined)}
        onToggleSidebar={vi.fn()}
        onToggleResultPanel={vi.fn()}
        {...props}
      />
    </ToastProvider>
  );
}

describe("Header", () => {
  it("shows the desktop open button only when the result panel is closed", async () => {
    const { rerender } = renderHeader();

    expect(screen.getByRole("button", { name: /open result panel/i })).toBeInTheDocument();

    rerender(
      <ToastProvider>
        <Header
          workspaceName="default"
          trustLabel="Visible SQL and governed execution."
          apiStatus="ok (dev)"
          canCopyChatHistory={false}
          hasResultPayload
          isResultPanelOpen
          onCopyChatHistory={vi.fn(async () => undefined)}
          onToggleSidebar={vi.fn()}
          onToggleResultPanel={vi.fn()}
        />
      </ToastProvider>
    );

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /open result panel/i })).not.toBeInTheDocument();
    });
  });

  it("does not render a duplicate new chat button in the header", () => {
    renderHeader();

    expect(screen.queryByRole("button", { name: /new chat/i })).not.toBeInTheDocument();
  });

  it("copies the full chat history from the header action", async () => {
    const onCopyChatHistory = vi.fn(async () => undefined);

    renderHeader({
      canCopyChatHistory: true,
      onCopyChatHistory,
    });

    fireEvent.click(screen.getByRole("button", { name: /copy chat/i }));

    await waitFor(() => {
      expect(onCopyChatHistory).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("does not render the profile menu in the header", () => {
    renderHeader();

    expect(screen.queryByRole("button", { name: /open profile menu/i })).not.toBeInTheDocument();
  });

  it("does not render a scenarios button in the header", () => {
    renderHeader();

    expect(screen.queryByRole("button", { name: /scenarios/i })).not.toBeInTheDocument();
  });
});
