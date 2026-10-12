import { NextRequest, NextResponse } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ scenarioId: string }> }
) {
  try {
    const { scenarioId } = await context.params;
    const payload = await request.json();
    const response = await fetchBackend(`/v1/scenarios/${scenarioId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "scenario_update_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ scenarioId: string }> }
) {
  try {
    const { scenarioId } = await context.params;
    const response = await fetchBackend(
      `/v1/scenarios/${scenarioId}?${request.nextUrl.searchParams.toString()}`,
      {
        method: "DELETE",
        cache: "no-store",
      },
    );
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      }),
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "scenario_archive_proxy_failed",
        message: error instanceof Error ? error.message : "Неизвестная ошибка",
      },
      { status: 500 }
    );
  }
}
