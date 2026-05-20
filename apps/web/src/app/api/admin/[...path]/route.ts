import { NextRequest, NextResponse } from "next/server";
import { fetchBackend } from "@/lib/backendAuth";

async function proxyAdminRequest(
  request: NextRequest,
  path: string[],
): Promise<NextResponse> {
  try {
    const search = request.nextUrl.searchParams.toString();
    const backendPath = `/v1/admin/${path.join("/")}${search ? `?${search}` : ""}`;
    const contentType = request.headers.get("content-type");
    const body =
      request.method === "GET" || request.method === "HEAD"
        ? undefined
        : await request.text();
    const response = await fetchBackend(backendPath, {
      method: request.method,
      headers: contentType ? { "Content-Type": contentType } : undefined,
      body,
      cache: "no-store",
    });
    if (response.status === 204 || response.status === 205) {
      return new NextResponse(null, {
        status: response.status,
      });
    }

    const responseBody = await response.text();

    return new NextResponse(responseBody, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "admin_proxy_failed",
        message: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 },
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyAdminRequest(request, path);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyAdminRequest(request, path);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyAdminRequest(request, path);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyAdminRequest(request, path);
}
