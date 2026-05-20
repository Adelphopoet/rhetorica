import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  WEB_SESSION_COOKIE_NAME,
  buildWebSessionCookieOptions,
} from "@/lib/backendAuth";

const apiBaseUrl = process.env.RHETORICA_API_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  try {
    const payload = await request.json();
    const response = await fetch(`${apiBaseUrl}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const rawBody = await response.text();
    const body = rawBody ? (JSON.parse(rawBody) as Record<string, unknown>) : {};

    if (!response.ok) {
      return NextResponse.json(
        { detail: body.detail ?? "login_failed" },
        { status: response.status },
      );
    }

    const accessToken = String(body.access_token ?? "");
    const expiresAt = typeof body.expires_at === "string" ? body.expires_at : undefined;
    const cookieStore = await cookies();
    cookieStore.set(
      WEB_SESSION_COOKIE_NAME,
      accessToken,
      buildWebSessionCookieOptions(expiresAt),
    );

    return NextResponse.json({
      workspace_id: body.workspace_id,
      role: body.role,
      expires_at: body.expires_at,
      user: body.user,
    });
  } catch {
    return NextResponse.json({ detail: "login_proxy_failed" }, { status: 500 });
  }
}
