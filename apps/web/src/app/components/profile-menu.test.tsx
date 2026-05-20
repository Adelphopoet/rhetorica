import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { describe, expect, it, vi } from "vitest";

const locationAssign = vi.fn();

vi.stubGlobal("fetch", vi.fn());
vi.stubGlobal("location", {
  ...window.location,
  assign: locationAssign,
});

import { ProfileMenu } from "./profile-menu";

function renderProfileMenu(props?: Partial<ComponentProps<typeof ProfileMenu>>) {
  return render(
    <ToastProvider>
      <ProfileMenu
        workspaceName="default"
        login="analyst"
        displayName="Ada Lovelace"
        role="admin"
        authProvider="password"
        canAccessAdmin
        {...props}
      />
    </ToastProvider>
  );
}

describe("ProfileMenu", () => {
  it("shows session details and admin entry when opened", async () => {
    renderProfileMenu();

    fireEvent.click(screen.getByRole("button", { name: /open profile menu/i }));

    const menu = await screen.findByRole("menu", { name: /profile menu/i });

    expect(menu).toBeInTheDocument();
    expect(within(menu).getByText("Ada Lovelace")).toBeInTheDocument();
    expect(within(menu).getByText("@analyst")).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /admin console/i })).toBeInTheDocument();
  });

  it("logs out and redirects to login", async () => {
    const fetchSpy = vi.mocked(fetch);
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "logged_out" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    renderProfileMenu();
    fireEvent.click(screen.getByRole("button", { name: /open profile menu/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /log out/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/auth/logout",
        expect.objectContaining({
          method: "POST",
        })
      );
    });
    expect(locationAssign).toHaveBeenCalledWith("/login");
  });

  it("hides the admin entry for non-admin users", async () => {
    renderProfileMenu({
      role: "analyst",
      canAccessAdmin: false,
    });

    fireEvent.click(screen.getByRole("button", { name: /open profile menu/i }));

    await waitFor(() => {
      expect(screen.queryByRole("menuitem", { name: /admin console/i })).not.toBeInTheDocument();
    });
  });

  it("applies custom content positioning classes when provided", async () => {
    renderProfileMenu({
      contentClassName: "left-0 right-auto w-full max-w-full",
    });

    fireEvent.click(screen.getByRole("button", { name: /open profile menu/i }));

    const menu = await screen.findByRole("menu", { name: /profile menu/i });

    expect(menu).toHaveClass("left-0", "right-auto", "w-full", "max-w-full");
  });
});
