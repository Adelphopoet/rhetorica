import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  WEB_SESSION_COOKIE_NAME,
  buildWebSessionCookieOptions,
  fetchBackend,
} from "@/lib/backendAuth";

export async function POST(
  _request: Request,
  context: { params: Promise<{ workspaceId: string }> },
) {
  try {
    const { workspaceId } = await context.params;
    const response = await fetchBackend(`/v1/workspaces/${encodeURIComponent(workspaceId)}/select`, {
      method: "POST",
    });
    const rawBody = await response.text();
    const body = rawBody ? (JSON.parse(rawBody) as Record<string, unknown>) : {};
    if (!response.ok) {
      return NextResponse.json({ detail: body.detail ?? "workspace_select_failed" }, { status: response.status });
    }
    const cookieStore = await cookies();
    cookieStore.set(
      WEB_SESSION_COOKIE_NAME,
      String(body.access_token ?? ""),
      buildWebSessionCookieOptions(typeof body.expires_at === "string" ? body.expires_at : undefined),
    );
    return NextResponse.json({
      workspace_id: body.workspace_id,
      home_workspace_id: body.home_workspace_id,
      workspace_role: body.workspace_role,
      expires_at: body.expires_at,
    });
  } catch {
    return NextResponse.json({ detail: "workspace_select_proxy_failed" }, { status: 500 });
  }
}
