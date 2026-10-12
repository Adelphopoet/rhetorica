import { NextRequest } from "next/server";
import { buildKeycloakCallbackResponse } from "@/lib/keycloakAuth";

export async function GET(request: NextRequest) {
  return buildKeycloakCallbackResponse(request);
}
