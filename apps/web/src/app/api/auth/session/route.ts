import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  WEB_SESSION_COOKIE_NAME,
  backendResponseHeaders,
  fetchBackend,
} from "@/lib/backendAuth";

export async function GET() {
  try {
    const response = await fetchBackend("/v1/auth/session");
    const body = await response.text();
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        const cookieStore = await cookies();
        cookieStore.delete(WEB_SESSION_COOKIE_NAME);
      }
      return new NextResponse(body, {
        status: response.status,
        headers: backendResponseHeaders(response, {
          "Content-Type": response.headers.get("Content-Type") ?? "application/json",
        }),
      });
    }

    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch {
    return NextResponse.json({ detail: "session_proxy_failed" }, { status: 500 });
  }
}
