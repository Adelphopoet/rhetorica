import { NextRequest } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ artifactId: string }> }
) {
  const { artifactId } = await context.params;
  const response = await fetchBackend(
    `/v1/files/${encodeURIComponent(artifactId)}/content`
  );
  const headers = new Headers();
  for (const name of [
    "content-type",
    "content-length",
    "content-disposition",
    "cache-control",
    "x-content-type-options",
  ]) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("X-Content-Type-Options", "nosniff");
  return new Response(response.body, {
    status: response.status,
    headers: backendResponseHeaders(response, headers),
  });
}
