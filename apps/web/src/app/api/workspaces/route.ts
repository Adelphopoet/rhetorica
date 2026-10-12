import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

export async function GET() {
  try {
    const response = await fetchBackend("/v1/workspaces");
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "workspace_proxy_failed" }, { status: 500 });
  }
}
