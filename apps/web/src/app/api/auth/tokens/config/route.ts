import { NextResponse } from "next/server";

export async function GET() {
  const apiUrl = (process.env.RHETORICA_PUBLIC_API_URL ?? "http://localhost:8000").replace(
    /\/$/,
    "",
  );
  return NextResponse.json({ mcp_url: `${apiUrl}/mcp` });
}
