import { NextRequest } from "next/server";
import { backendResponseHeaders, fetchBackend } from "@/lib/backendAuth";

function sanitizeChatAskPayload(body: unknown) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return body;
  }

  const payload = body as Record<string, unknown>;
  return {
    question: payload.question,
    workspace_id: payload.workspace_id,
    conversation_id: payload.conversation_id,
    agent_id: payload.agent_id,
    attachment_ids: payload.attachment_ids,
    run_sql: payload.run_sql,
    coding_mode: payload.coding_mode,
    coding_resources: payload.coding_resources,
    selected_clarification_id: payload.selected_clarification_id,
    selected_clarification_value: payload.selected_clarification_value,
  };
}

export async function POST(request: NextRequest) {
  try {
    const body = sanitizeChatAskPayload(await request.json());
    const runId = request.headers.get("x-rhetorica-run-id");

    // Forward to backend SSE endpoint
    const response = await fetchBackend("/v1/chat/ask/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        ...(runId ? { "X-Rhetorica-Run-Id": runId } : {}),
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });

    if (!response.ok) {
      const errorText = await response.text();
      return new Response(
        JSON.stringify({
          error: "chat_api_error",
          status: response.status,
          body: errorText.slice(0, 500),
        }),
        { 
          status: response.status,
          headers: backendResponseHeaders(response, {
            "Content-Type": "application/json",
          }),
        }
      );
    }

    // Stream SSE response back to client
    return new Response(response.body, {
      status: response.status,
      headers: backendResponseHeaders(response, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
      }),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      // Client disconnected - this is normal
      return new Response(null, { status: 499 });
    }
    
    return new Response(
      JSON.stringify({ error: "chat_proxy_failed", message: error instanceof Error ? error.message : "Неизвестная ошибка" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}
