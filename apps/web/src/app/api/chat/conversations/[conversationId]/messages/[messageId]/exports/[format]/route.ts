import { NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(
  _request: Request,
  context: {
    params: Promise<{
      conversationId: string;
      messageId: string;
      format: string;
    }>;
  }
) {
  try {
    const { conversationId, messageId, format } = await context.params;
    const response = await fetchBackend(
      `/v1/chat/conversation/${conversationId}/message/${messageId}/export/${format}`,
      {
        cache: "no-store",
      },
    );

    const body = await response.arrayBuffer();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "analysis_export_api_error",
          status: response.status,
          body: Buffer.from(body).toString("utf-8").slice(0, 500),
        },
        { status: response.status, headers: backendResponseHeaders(response) }
      );
    }

    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
        "Content-Disposition":
          response.headers.get("Content-Disposition") ?? 'attachment; filename="analysis-export"',
        "Cache-Control": "no-store",
      }),
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "analysis_export_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}
