import { NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ tokenId: string }> },
) {
  const { tokenId } = await context.params;
  try {
    const response = await fetchBackend(`/v1/auth/tokens/${encodeURIComponent(tokenId)}`, {
      method: "DELETE",
    });
    if (response.status === 204) {
      return new NextResponse(null, {
        status: 204,
        headers: backendResponseHeaders(response),
      });
    }
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch {
    return NextResponse.json({ detail: "api_token_revoke_proxy_failed" }, { status: 500 });
  }
}
