import { NextRequest } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ artifactId: string }> }
) {
  const { artifactId } = await context.params;
  const response = await fetchBackend(`/v1/files/${encodeURIComponent(artifactId)}`);
  return new Response(response.body, {
    status: response.status,
    headers: backendResponseHeaders(response, {
      "Content-Type": response.headers.get("content-type") ?? "application/json",
    }),
  });
}

export async function DELETE(
  _request: NextRequest,
  context: { params: Promise<{ artifactId: string }> }
) {
  const { artifactId } = await context.params;
  const response = await fetchBackend(`/v1/files/${encodeURIComponent(artifactId)}`, {
    method: "DELETE",
  });
  return new Response(response.body, {
    status: response.status,
    headers: backendResponseHeaders(response),
  });
}
