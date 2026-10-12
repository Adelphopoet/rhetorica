import { NextRequest, NextResponse } from "next/server";
import {
  WEB_SESSION_COOKIE_NAME,
  buildWebSessionCookieOptions,
} from "@/lib/backendAuth";
import { safeNextPath } from "@/lib/safeNextPath";

const apiBaseUrl = process.env.RHETORICA_API_URL ?? "http://localhost:8000";
const stateCookieName = "rhetorica_keycloak_state";
const nextCookieName = "rhetorica_keycloak_next";

type TokenResponse = {
  access_token?: string;
  id_token?: string;
  expires_in?: number;
  token_type?: string;
};

type BackendSessionResponse = {
  access_token?: string;
  expires_at?: string;
};

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name}_required`);
  }
  return value;
}

function issuerUrl(): string {
  return requiredEnv("RHETORICA_KEYCLOAK_ISSUER_URL").replace(/\/+$/, "");
}

function clientId(): string {
  return requiredEnv("RHETORICA_KEYCLOAK_CLIENT_ID");
}

function clientSecret(): string {
  return requiredEnv("RHETORICA_KEYCLOAK_CLIENT_SECRET");
}

function publicBaseUrl(request: NextRequest): string {
  const configuredUrl = process.env.RHETORICA_PUBLIC_URL?.trim().replace(/\/+$/, "");
  if (configuredUrl) {
    return configuredUrl;
  }
  const forwardedProto = request.headers.get("x-forwarded-proto") ?? "https";
  const forwardedHost = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (forwardedHost) {
    return `${forwardedProto}://${forwardedHost}`;
  }
  return request.nextUrl.origin;
}

function redirectUri(request: NextRequest): string {
  return (
    process.env.RHETORICA_KEYCLOAK_REDIRECT_URI?.trim() ||
    new URL("/api/auth/keycloak/callback", publicBaseUrl(request)).toString()
  );
}

function postLogoutRedirectUri(request: NextRequest): string {
  return (
    process.env.RHETORICA_KEYCLOAK_POST_LOGOUT_REDIRECT_URI?.trim() ||
    new URL("/login", publicBaseUrl(request)).toString()
  );
}

function transientCookieOptions() {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: 600,
  };
}

export function buildKeycloakLoginResponse(request: NextRequest): NextResponse {
  const state = crypto.randomUUID();
  const nextPath = safeNextPath(request.nextUrl.searchParams.get("next"));
  const authUrl = new URL(`${issuerUrl()}/protocol/openid-connect/auth`);
  authUrl.searchParams.set("client_id", clientId());
  authUrl.searchParams.set("redirect_uri", redirectUri(request));
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("scope", "openid profile email groups");
  authUrl.searchParams.set("state", state);

  const response = NextResponse.redirect(authUrl);
  response.cookies.set(stateCookieName, state, transientCookieOptions());
  response.cookies.set(nextCookieName, nextPath, transientCookieOptions());
  return response;
}

async function exchangeCodeForTokens(request: NextRequest, code: string): Promise<TokenResponse> {
  const response = await fetch(`${issuerUrl()}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri(request),
      client_id: clientId(),
      client_secret: clientSecret(),
    }),
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`keycloak_token_exchange_failed:${response.status}:${body.slice(0, 200)}`);
  }
  return (await response.json()) as TokenResponse;
}

async function createBackendSession(idToken: string): Promise<BackendSessionResponse> {
  const response = await fetch(`${apiBaseUrl}/v1/auth/keycloak/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: idToken }),
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`backend_keycloak_session_failed:${response.status}:${body.slice(0, 200)}`);
  }
  return (await response.json()) as BackendSessionResponse;
}

function loginRedirect(request: NextRequest, error: string): NextResponse {
  const url = new URL("/login", publicBaseUrl(request));
  url.searchParams.set("error", error);
  return NextResponse.redirect(url);
}

export function buildLoginErrorResponse(request: NextRequest, error: string): NextResponse {
  return loginRedirect(request, error);
}

export async function buildKeycloakCallbackResponse(request: NextRequest): Promise<NextResponse> {
  const error = request.nextUrl.searchParams.get("error");
  if (error) {
    return loginRedirect(request, error);
  }

  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const expectedState = request.cookies.get(stateCookieName)?.value;
  if (!code || !state || state !== expectedState) {
    return loginRedirect(request, "invalid_keycloak_state");
  }

  const nextPath = safeNextPath(request.cookies.get(nextCookieName)?.value ?? null);
  try {
    const tokens = await exchangeCodeForTokens(request, code);
    if (!tokens.id_token) {
      return loginRedirect(request, "missing_keycloak_id_token");
    }
    const session = await createBackendSession(tokens.id_token);
    if (!session.access_token) {
      return loginRedirect(request, "missing_backend_access_token");
    }
    const response = NextResponse.redirect(new URL(nextPath, publicBaseUrl(request)));
    response.cookies.set(
      WEB_SESSION_COOKIE_NAME,
      session.access_token,
      buildWebSessionCookieOptions(session.expires_at),
    );
    response.cookies.delete(stateCookieName);
    response.cookies.delete(nextCookieName);
    return response;
  } catch (error) {
    console.error("keycloak.callback_failed", error);
    return loginRedirect(request, "keycloak_login_failed");
  }
}

export function buildKeycloakLogoutUrl(request: NextRequest): string | null {
  const issuer = process.env.RHETORICA_KEYCLOAK_ISSUER_URL?.trim().replace(/\/+$/, "");
  const client = process.env.RHETORICA_KEYCLOAK_CLIENT_ID?.trim();
  if (!issuer || !client) {
    return null;
  }
  const logoutUrl = new URL(`${issuer}/protocol/openid-connect/logout`);
  logoutUrl.searchParams.set("client_id", client);
  logoutUrl.searchParams.set("post_logout_redirect_uri", postLogoutRedirectUri(request));
  return logoutUrl.toString();
}
