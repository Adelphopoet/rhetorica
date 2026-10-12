import { afterEach, expect, it, vi } from "vitest";
import { requestWorkflowProposal } from "./workflowProposalApi";

afterEach(() => vi.unstubAllGlobals());
const request = { workspace_id: "default", agent_id: "agent", instruction: "Edit", expected_revision: 1 };

function streamed(chunks: Uint8Array[]) {
  const cancel = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new ReadableStream({
    start(controller) { chunks.forEach(chunk => controller.enqueue(chunk)); controller.close(); }, cancel,
  }), { headers: { "Content-Type": "text/event-stream" } })));
  return cancel;
}

it("reads split UTF-8 progress events and returns only the completed proposal", async () => {
  const progress = { phase: "generating", message: "Собираю сценарий", batch: 2, node_count: 4 };
  const proposal = { summary: "Готово", workflow: { nodes: [] }, base_revision: 1, validation: { valid: true } };
  const bytes = new TextEncoder().encode(`: keep-alive\n\nevent: progress\ndata: ${JSON.stringify(progress)}\n\nevent: proposal\ndata: ${JSON.stringify(proposal)}\n\n`);
  streamed([...bytes].map(byte => new Uint8Array([byte])));
  const onProgress = vi.fn();
  expect(await requestWorkflowProposal("s", request, onProgress, new AbortController().signal)).toEqual(proposal);
  expect(onProgress).toHaveBeenCalledExactlyOnceWith(progress);
});

it("surfaces typed generation failures", async () => {
  streamed([new TextEncoder().encode('event: error\ndata: {"detail":"Invalid graph","code":"scenario_ai_invalid_output","status":422}\n\n')]);
  await expect(requestWorkflowProposal("s", request, vi.fn(), new AbortController().signal)).rejects.toMatchObject({ code: "scenario_ai_invalid_output", status: 422 });
});

it("rejects a disconnected stream instead of treating partial progress as success", async () => {
  streamed([new TextEncoder().encode(': keep-alive\n\n')]);
  await expect(requestWorkflowProposal("s", request, vi.fn(), new AbortController().signal)).rejects.toThrow("Соединение прервалось");
});

it("passes cancellation to the request", async () => {
  const controller = new AbortController();
  controller.abort();
  const fetchMock = vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"));
  vi.stubGlobal("fetch", fetchMock);
  await expect(requestWorkflowProposal("s", request, vi.fn(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
});
