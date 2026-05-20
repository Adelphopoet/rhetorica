import { NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

export async function POST(
  request: Request,
  context: { params: Promise<{ messageId: string }> }
) {
  try {
    const { messageId } = await context.params;
    const payload = await request.json();
    const response = await fetchBackend(`/v1/chat/message/${messageId}/repair-turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });

    const body = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "chat_repair_turn_api_error",
          status: response.status,
          body: body.slice(0, 500),
        },
        { status: response.status }
      );
    }

    return new NextResponse(body, {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "chat_repair_turn_proxy_failed",
        message: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
