import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OAuthConsentClient } from "./OAuthConsentClient";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OAuthConsentClient", () => {
  it("shows verified client/scopes and follows approve redirect", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            client: { id: "rh_oc_test", name: "Claude", uri: "https://claude.ai" },
            redirect_host: "claude.ai",
            scopes: ["mcp:read", "mcp:execute"],
            resource: "https://api.example/mcp",
            expires_at: "2026-09-01T12:00:00Z",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ redirect_uri: "https://claude.ai/callback?code=ok" }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const onRedirect = vi.fn();
    const user = userEvent.setup();

    render(<OAuthConsentClient transaction="rh_otx_test.secret" onRedirect={onRedirect} />);

    expect(await screen.findByText("Claude")).toBeInTheDocument();
    expect(screen.getByText(/Смотреть агентов/)).toBeInTheDocument();
    expect(screen.getByText(/Отправлять сообщения агентам/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Разрешить" }));

    await waitFor(() =>
      expect(onRedirect).toHaveBeenCalledWith("https://claude.ai/callback?code=ok"),
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/oauth/consent/rh_otx_test.secret/approve",
      { method: "POST" },
    );
  });

  it("sends an explicit deny decision", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            client: { id: "rh_oc_test", name: "Inspector", uri: null },
            redirect_host: "127.0.0.1",
            scopes: ["mcp:read"],
            resource: "http://localhost:8000/mcp",
            expires_at: "2026-09-01T12:00:00Z",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ redirect_uri: "http://127.0.0.1/callback?error=access_denied" }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const onRedirect = vi.fn();
    const user = userEvent.setup();
    render(<OAuthConsentClient transaction="rh_otx_test.secret" onRedirect={onRedirect} />);
    await screen.findByText("Inspector");

    await user.click(screen.getByRole("button", { name: "Отказать" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/oauth/consent/rh_otx_test.secret/deny",
      { method: "POST" },
    );
  });
});
