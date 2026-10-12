import { NextRequest, NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(request: NextRequest) {
  try {
    const response = await fetchBackend(
      `/v1/chat/conversations?${request.nextUrl.searchParams.toString()}`,
      {
        cache: "no-store",
      },
    );

    const body = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "chat_conversations_api_error",
          status: response.status,
          body: body.slice(0, 500),
        },
        { status: response.status, headers: backendResponseHeaders(response) }
      );
    }

    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "chat_conversations_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  try {
    const payload = await request.json();
    const response = await fetchBackend("/v1/chat/conversation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });

    const body = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "chat_conversation_create_api_error",
          status: response.status,
          body: body.slice(0, 500),
        },
        { status: response.status, headers: backendResponseHeaders(response) }
      );
    }

    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "chat_conversation_create_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}
