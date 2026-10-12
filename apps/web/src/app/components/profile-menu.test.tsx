import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
        apiStatus="ok (dev)"
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
  beforeEach(() => {
    vi.mocked(fetch).mockReset();
    locationAssign.mockReset();
  });

  it("shows session details and admin entry when opened", async () => {
    renderProfileMenu();

    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));

    const menu = await screen.findByRole("menu", { name: /меню профиля/i });

    expect(menu).toBeInTheDocument();
    expect(within(menu).getByText("analyst")).toBeInTheDocument();
    expect(within(menu).queryByText("Ada Lovelace")).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /администрирование/i })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /api & mcp токены/i })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /^документация$/i })).toHaveAttribute(
      "href",
      "https://example.com/docs/rhetorica"
    );
  });

  it("reveals API status in a tooltip on hover", async () => {
    renderProfileMenu();

    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));
    const menu = await screen.findByRole("menu", { name: /меню профиля/i });

    const tooltipTrigger = menu.querySelector("svg")?.parentElement?.parentElement as HTMLElement;
    expect(tooltipTrigger).toBeTruthy();

    fireEvent.mouseEnter(tooltipTrigger);
    fireEvent.mouseOver(tooltipTrigger);

    expect(screen.getByText("API: ok (dev)")).toBeInTheDocument();
  });

  it("localizes the role label", async () => {
    renderProfileMenu({ role: "admin" });

    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));
    const menu = await screen.findByRole("menu", { name: /меню профиля/i });

    expect(within(menu).getByText("Админ")).toBeInTheDocument();
  });

  it("opens API token management from the profile menu", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/config")) {
        return new Response(JSON.stringify({ mcp_url: "http://localhost:8000/mcp" }), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });

    renderProfileMenu();
    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /api & mcp токены/i }));

    expect(await screen.findByRole("dialog")).toHaveTextContent("API & MCP токены");
    expect(screen.getByLabelText(/название токена/i)).toHaveValue("Мой MCP-клиент");
    expect(screen.getByLabelText(/^срок действия$/i)).toHaveValue("90");
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
    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /выйти/i }));

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

    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));

    await waitFor(() => {
      expect(screen.queryByRole("menuitem", { name: /admin console/i })).not.toBeInTheDocument();
    });
  });

  it("applies custom content positioning classes when provided", async () => {
    renderProfileMenu({
      contentClassName: "left-0 right-auto w-full max-w-full",
    });

    fireEvent.click(screen.getByRole("button", { name: /открыть меню профиля/i }));

    const menu = await screen.findByRole("menu", { name: /меню профиля/i });

    expect(menu).toHaveClass("left-0", "right-auto", "w-full", "max-w-full");
  });
});
