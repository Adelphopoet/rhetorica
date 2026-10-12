import { NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(
  _request: Request,
  context: { params: Promise<{ transaction: string }> },
) {
  const { transaction } = await context.params;
  try {
    const response = await fetchBackend(
      `/v1/oauth/consent/${encodeURIComponent(transaction)}`,
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
