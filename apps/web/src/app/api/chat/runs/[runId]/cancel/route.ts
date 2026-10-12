import { NextRequest } from "next/server";

import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ runId: string }> }
) {
  const { runId } = await context.params;
  const workspaceId = request.nextUrl.searchParams.get("workspace_id") ?? "default";
  const response = await fetchBackend(
    `/v1/chat/runs/${encodeURIComponent(runId)}/cancel?workspace_id=${encodeURIComponent(workspaceId)}`,
    { method: "POST", cache: "no-store" }
  );
  return new Response(response.body, {
    status: response.status,
    headers: backendResponseHeaders(response, {
      "Content-Type": response.headers.get("Content-Type") ?? "application/json",
    }),
  });
}
