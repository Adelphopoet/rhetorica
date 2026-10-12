import { NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

async function proxy(response: Response): Promise<NextResponse> {
  if (response.status === 204 || response.status === 205) {
    return new NextResponse(null, {
      status: response.status,
      headers: backendResponseHeaders(response),
    });
  }
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: backendResponseHeaders(response, {
      "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      "Cache-Control": "no-store",
    }),
  });
}

export async function GET() {
  try {
    return proxy(await fetchBackend("/v1/auth/tokens"));
  } catch {
    return NextResponse.json({ detail: "api_tokens_proxy_failed" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    return proxy(
      await fetchBackend("/v1/auth/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: await request.text(),
      }),
    );
  } catch {
    return NextResponse.json({ detail: "api_token_create_proxy_failed" }, { status: 500 });
  }
}
