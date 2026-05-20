import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { WEB_SESSION_COOKIE_NAME } from "@/lib/backendAuth";

export async function POST() {
  const cookieStore = await cookies();
  cookieStore.delete(WEB_SESSION_COOKIE_NAME);
  return NextResponse.json({ status: "logged_out" });
}
