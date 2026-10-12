import { workflowUrl, WorkflowApiError, type AiProposal } from "./workflowApi";

export type AiProgress = { phase: "generating" | "validating" | "repairing"; message: string; batch: number; node_count: number };

export async function requestWorkflowProposal(
  scenarioId: string,
  payload: { workspace_id: string; agent_id: string; instruction: string; expected_revision: number },
  onProgress: (progress: AiProgress) => void,
  signal: AbortSignal,
): Promise<AiProposal> {
  const response = await fetch(workflowUrl(`scenarios/${scenarioId}/ai-proposals`), {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload), signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new WorkflowApiError(typeof error.detail === "string" ? error.detail : `Ошибка сервера (${response.status})`, response.status, error.code);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new WorkflowApiError("Сервер не открыл поток ответа", 502);
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const lines = buffer.slice(0, boundary).split("\n");
        buffer = buffer.slice(boundary + 2);
        const event = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
        const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const payload = JSON.parse(data);
        if (event === "progress") onProgress(payload as AiProgress);
        if (event === "proposal") return payload as AiProposal;
        if (event === "error") throw new WorkflowApiError(payload.detail, payload.status ?? 422, payload.code);
      }
      if (buffer.length > 2 * 1024 * 1024) throw new WorkflowApiError("Ответ помощника слишком большой", 502);
      if (done) throw new WorkflowApiError("Соединение прервалось до готового предложения. Повторите запрос", 502);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
