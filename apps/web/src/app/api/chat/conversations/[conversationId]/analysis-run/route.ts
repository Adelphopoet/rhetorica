import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

async function proxyAnalysisRun(
  conversationId: string,
  suffix = "",
  init: RequestInit = {}
) {
  const response = await fetchBackend(
    `/v1/chat/conversation/${conversationId}/analysis-run${suffix}`,
    {
      ...init,
      cache: "no-store",
    }
  );
  const body = await response.text();
  if (!response.ok) {
    return NextResponse.json(
      {
        error: "analysis_run_api_error",
        status: response.status,
        body: body.slice(0, 500),
      },
      { status: response.status }
    );
  }
  return new NextResponse(body, {
    status: response.status,
    headers: {
      "Content-Type": response.headers.get("Content-Type") ?? "application/json",
    },
  });
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ conversationId: string }> }
) {
  try {
    const { conversationId } = await context.params;
    return await proxyAnalysisRun(conversationId);
  } catch (error) {
    return NextResponse.json(
      {
        error: "analysis_run_proxy_failed",
        message: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
