import { beforeEach, describe, expect, it, vi } from "vitest";

const cookieGet = vi.fn();

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: cookieGet,
  })),
}));

describe("backendAuth", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
    cookieGet.mockReset();
    process.env.RHETORICA_API_URL = "http://api.test";
  });

  it("returns null when authenticated session cookie is absent", async () => {
    cookieGet.mockReturnValue(undefined);
    const authModule = await import("./backendAuth");

    await expect(authModule.getBackendAccessToken()).resolves.toBeNull();
  });

  it("fetchBackend injects bearer auth from server-side session cookie", async () => {
    cookieGet.mockReturnValue({ value: "session-token" });
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    const authModule = await import("./backendAuth");
    const response = await authModule.fetchBackend("/v1/chat/conversations");

    expect(response.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://api.test/v1/chat/conversations",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );

    const backendHeaders = fetchSpy.mock.calls[0]?.[1]?.headers;
    expect(backendHeaders).toBeInstanceOf(Headers);
    expect((backendHeaders as Headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("forwards only public rate-limit response headers", async () => {
    const authModule = await import("./backendAuth");
    const backendResponse = new Response(null, {
      headers: {
        "Retry-After": "120",
        "RateLimit-Limit": "20",
        "RateLimit-Remaining": "0",
        "RateLimit-Reset": "120",
        "X-Backend-Secret": "do-not-forward",
      },
    });

    const headers = authModule.backendResponseHeaders(backendResponse, {
      "Content-Type": "application/json",
    });

    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Retry-After")).toBe("120");
    expect(headers.get("RateLimit-Limit")).toBe("20");
    expect(headers.get("RateLimit-Remaining")).toBe("0");
    expect(headers.get("RateLimit-Reset")).toBe("120");
    expect(headers.has("X-Backend-Secret")).toBe(false);
  });
});
