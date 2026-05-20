import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

export async function POST(
  _request: Request,
  context: { params: Promise<{ conversationId: string }> }
) {
  try {
    const { conversationId } = await context.params;
    const response = await fetchBackend(
      `/v1/chat/conversation/${conversationId}/analysis-run/resume`,
      {
        method: "POST",
        cache: "no-store",
      }
    );
    const body = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "analysis_run_resume_api_error",
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
  } catch (error) {
    return NextResponse.json(
      {
        error: "analysis_run_resume_proxy_failed",
        message: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
