import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { buildKeycloakLogoutUrl } from "@/lib/keycloakAuth";
import { WEB_SESSION_COOKIE_NAME } from "@/lib/backendAuth";

export async function POST(request: NextRequest) {
  const cookieStore = await cookies();
  cookieStore.delete(WEB_SESSION_COOKIE_NAME);
  return NextResponse.json({
    status: "logged_out",
    logout_url: buildKeycloakLogoutUrl(request),
  });
}
