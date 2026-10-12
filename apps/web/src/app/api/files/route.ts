import { NextRequest } from "next/server";
import {
  backendResponseHeaders,
  buildBackendHeaders,
  fetchBackend,
} from "@/lib/backendAuth";

export async function POST(request: NextRequest) {
  const headers = await buildBackendHeaders({
    "Content-Type": request.headers.get("content-type") ?? "multipart/form-data",
  });
  const init: RequestInit & { duplex: "half" } = {
    method: "POST",
    headers,
    body: request.body,
    duplex: "half",
    cache: "no-store",
  };
  const response = await fetchBackend("/v1/files", init);
  return new Response(response.body, {
    status: response.status,
    headers: backendResponseHeaders(response, {
      "Content-Type": response.headers.get("content-type") ?? "application/json",
    }),
  });
}
