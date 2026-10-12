import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

type RouteContext = { params: Promise<{ path?: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  try {
    const { path = [] } = await context.params;
    const url = new URL(request.url);
    const suffix = path.length > 0 ? `/${path.map(encodeURIComponent).join("/")}` : "";
    const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
    const response = await fetchBackend(`/v1/admin/workspaces${suffix}${url.search}`, {
      method: request.method,
      headers: body ? { "Content-Type": request.headers.get("Content-Type") ?? "application/json" } : undefined,
      body: body || undefined,
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "workspace_admin_proxy_failed" }, { status: 500 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
