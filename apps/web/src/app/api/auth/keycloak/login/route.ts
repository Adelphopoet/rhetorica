import { NextRequest } from "next/server";
import { buildKeycloakLoginResponse, buildLoginErrorResponse } from "@/lib/keycloakAuth";

export async function GET(request: NextRequest) {
  try {
    return buildKeycloakLoginResponse(request);
  } catch {
    return buildLoginErrorResponse(request, "keycloak_auth_misconfigured");
  }
}
