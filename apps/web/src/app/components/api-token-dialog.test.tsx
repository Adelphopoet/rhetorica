import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ToastProvider } from "@rhetorica/ui";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiTokenDialog,
  toApiTokenMetadata,
  updateTokenScopes,
  type CreatedApiToken,
} from "./api-token-dialog";

const createdToken = {
  id: "0f9d8056-4b50-46d2-a0d0-76d94f61274d",
  name: "My MCP client",
  token: "rh_pat_token-id.super-secret",
  token_type: "bearer",
  token_hint: "cret",
  scopes: ["mcp:read", "mcp:execute"],
  lifetime_days: 90,
  auto_renew: false,
  expires_at: "2026-11-30T00:00:00Z",
  last_used_at: null,
  revoked_at: null,
  created_at: "2026-09-01T00:00:00Z",
  is_active: true,
} satisfies CreatedApiToken;

describe("ApiTokenDialog", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/config")) {
        return new Response(JSON.stringify({ mcp_url: "https://rhetorica.example/mcp" }), { status: 200 });
      }
      if (url === "/api/auth/tokens" && init?.method === "POST") {
        return new Response(JSON.stringify(createdToken), { status: 201 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("creates a 90-day scoped token and shows client configuration once", async () => {
    render(
      <ToastProvider>
        <ApiTokenDialog open onOpenChange={vi.fn()} />
      </ToastProvider>,
    );

    await screen.findByText("Токенов пока нет");
    fireEvent.click(screen.getByRole("button", { name: /создать токен/i }));

    await waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        "/api/auth/tokens",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const createCall = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      name: "Мой MCP-клиент",
      scopes: ["mcp:read", "mcp:execute"],
      lifetime_days: 90,
      auto_renew: false,
    });
    expect(await screen.findByLabelText("Новый API токен")).toHaveValue(createdToken.token);
    expect(screen.getByText("https://rhetorica.example/mcp")).toBeInTheDocument();
    expect(screen.getByText(/инструмент openai responses/i)).toBeInTheDocument();
    expect(screen.getByText(/mcp-сервер claude api/i)).toBeInTheDocument();
    expect(screen.getByText(/"allowed_tools"/i)).toBeInTheDocument();
    expect(screen.getByText(/"require_approval": "always"/i)).toBeInTheDocument();
  });

  it("keeps the one-time secret out of token-list state", () => {
    const metadata = toApiTokenMetadata(createdToken);

    expect(metadata).not.toHaveProperty("token");
    expect(metadata).not.toHaveProperty("token_type");
    expect(metadata.id).toBe(createdToken.id);
  });

  it("keeps MCP execute and discovery scopes coherent", () => {
    expect(updateTokenScopes([], "mcp:execute", true)).toEqual([
      "mcp:read",
      "mcp:execute",
    ]);
    expect(
      updateTokenScopes(["mcp:read", "mcp:execute"], "mcp:read", false),
    ).toEqual([]);
  });
});
