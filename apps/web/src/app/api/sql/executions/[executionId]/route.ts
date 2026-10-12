import { NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function GET(
  _request: Request,
  context: { params: Promise<{ executionId: string }> }
) {
  try {
    const { executionId } = await context.params;
    const response = await fetchBackend(`/v1/sql/executions/${executionId}`, {
      cache: "no-store",
    });

    const body = await response.text();
    if (!response.ok) {
      return NextResponse.json(
        {
          error: "sql_execution_detail_api_error",
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
        error: "sql_execution_detail_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}
