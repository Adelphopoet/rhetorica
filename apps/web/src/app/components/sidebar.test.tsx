import { render, screen } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { describe, expect, it, vi } from "vitest";

import { Sidebar } from "./sidebar";

function renderSidebar(canAccessAdmin: boolean) {
  return render(
    <ToastProvider>
      <Sidebar
        isOpen
        onClose={vi.fn()}
        workspaceName="default"
        canAccessAdmin={canAccessAdmin}
        userLogin="analyst"
        userDisplayName="Ada Lovelace"
        userRole={canAccessAdmin ? "admin" : "analyst"}
        userAuthProvider="password"
        conversations={[]}
        activeConversationId={null}
        historyLoading={false}
        interactionDisabled={false}
        deletingConversationId={null}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />
    </ToastProvider>,
  );
}

describe("Sidebar", () => {
  it("does not render logout in the sidebar anymore", () => {
    renderSidebar(true);

    expect(screen.queryByRole("button", { name: /log out/i })).not.toBeInTheDocument();
  });

  it("renders the profile menu trigger instead of the brand plaque", () => {
    renderSidebar(true);

    expect(screen.getByRole("button", { name: /open profile menu/i })).toBeInTheDocument();
    expect(screen.queryByText("Rhetorica")).not.toBeInTheDocument();
  });

  it("does not show the old admin shortcut in the sidebar", () => {
    renderSidebar(false);

    expect(screen.queryByLabelText("Open admin")).not.toBeInTheDocument();
  });

  it("does not render a scenarios button in the sidebar", () => {
    renderSidebar(true);

    expect(screen.queryByRole("button", { name: /scenarios/i })).not.toBeInTheDocument();
  });
});
