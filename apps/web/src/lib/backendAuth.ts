import { cookies } from "next/headers";

const apiBaseUrl = process.env.RHETORICA_API_URL ?? "http://localhost:8000";
export const WEB_SESSION_COOKIE_NAME =
  process.env.RHETORICA_SESSION_COOKIE_NAME ?? "rhetorica_session";

const FORWARDED_RATE_LIMIT_HEADERS = [
  "Retry-After",
  "RateLimit-Limit",
  "RateLimit-Remaining",
  "RateLimit-Reset",
] as const;

type SessionCookieOptions = {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires?: Date;
};

export async function getBackendAccessToken(): Promise<string | null> {
  const cookieStore = await cookies();
  return cookieStore.get(WEB_SESSION_COOKIE_NAME)?.value ?? null;
}

export function buildWebSessionCookieOptions(expiresAt?: string): SessionCookieOptions {
  const expires = expiresAt ? new Date(expiresAt) : undefined;
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: expires && Number.isFinite(expires.getTime()) ? expires : undefined,
  };
}

export async function buildBackendHeaders(headers?: HeadersInit): Promise<Headers> {
  const result = new Headers(headers);
  const accessToken = await getBackendAccessToken();
  if (accessToken && !result.has("Authorization")) {
    result.set("Authorization", `Bearer ${accessToken}`);
  }
  return result;
}

export async function fetchBackend(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = await buildBackendHeaders(init.headers);
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers,
    cache: init.cache ?? "no-store",
  });
}

export function backendResponseHeaders(
  response: Response,
  initialHeaders?: HeadersInit,
): Headers {
  const headers = new Headers(initialHeaders);
  for (const name of FORWARDED_RATE_LIMIT_HEADERS) {
    const value = response.headers.get(name);
    if (value !== null) {
      headers.set(name, value);
    }
  }
  return headers;
}
