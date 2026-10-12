import { NextRequest, NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

function originAllowed(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return true;
  }
  const configured = process.env.RHETORICA_PUBLIC_URL?.replace(/\/+$/, "");
  const allowed = new Set([request.nextUrl.origin, configured].filter(Boolean));
  if (process.env.NODE_ENV !== "production") {
    allowed.add("http://localhost:3000");
    allowed.add("http://127.0.0.1:3000");
  }
  return allowed.has(origin);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ transaction: string; action: string }> },
) {
  if (!originAllowed(request)) {
    return NextResponse.json({ detail: "invalid_request_origin" }, { status: 403 });
  }
  const { transaction, action } = await context.params;
  if (action !== "approve" && action !== "deny") {
    return NextResponse.json({ detail: "oauth_consent_action_not_found" }, { status: 404 });
  }
  try {
    const response = await fetchBackend(
      `/v1/oauth/consent/${encodeURIComponent(transaction)}/${action}`,
      { method: "POST" },
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
      }),
    });
  } catch {
    return NextResponse.json({ detail: "oauth_consent_proxy_failed" }, { status: 500 });
  }
}
