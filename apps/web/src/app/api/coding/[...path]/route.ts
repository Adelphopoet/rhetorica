import { NextRequest, NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!["coding-runs", "coding-capabilities", "coding-plans", "coding-workspace-settings", "coding-agent-teams", "coding-team-options"].includes(path[0]) || path.some(part => !/^[a-zA-Z0-9_-]+$/.test(part))) return NextResponse.json({ detail: "Неизвестный маршрут" }, { status: 404 });
  const headers = new Headers();
  for (const name of ["Accept", "Content-Type", "Idempotency-Key", "Last-Event-ID"]) {
    const value = request.headers.get(name); if (value) headers.set(name, value);
  }
  try {
    const response = await fetchBackend(`/v1/${path.join("/")}${request.nextUrl.search}`, {
      method: request.method, headers, cache: "no-store", signal: request.signal,
      body: request.method === "GET" ? undefined : await request.text(),
    });
    return new NextResponse(response.body, { status: response.status, headers: backendResponseHeaders(response, {
      "Content-Type": response.headers.get("Content-Type") ?? "application/json", "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    }) });
  } catch { return NextResponse.json({ detail: "Сервис разработки недоступен" }, { status: 502 }); }
}
export { proxy as GET, proxy as POST, proxy as PUT };
