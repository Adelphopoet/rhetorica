import { expect, it } from "vitest";
import { buildChatTimeline } from "./chatTimeline";
import type { ChatMessageViewModel } from "./chatViewModel";
import type { CodingRun } from "@/features/coding/codingApi";
const message = (id: string, time: number): ChatMessageViewModel => ({ id, role: "user", text: id, timestamp: new Date(time), deliveryState: "complete" });
const run = (id: string, time: number): CodingRun => ({ id, created_at: new Date(time).toISOString(), status: "running", phase: "developing", deadline: "", repository: { project_id: "project", base_sha: "base", target_branch: "main" } });
it("keeps old runs before later messages when new messages arrive or history is rehydrated", () => {
  const messages = [message("plan", 100), message("followup", 300), message("new", 500)];
  const runs = [run("second", 400), run("first", 200)];
  const expected = ["message:plan", "coding:first", "message:followup", "coding:second", "message:new"];
  expect(buildChatTimeline(messages, runs).map(item => item.id)).toEqual(expected);
  expect(buildChatTimeline(messages.map(item => ({ ...item })), runs.map(item => ({ ...item, status: "succeeded" }))).map(item => item.id)).toEqual(expected);
});
it("preserves live message order and deterministic ordering for simultaneous runs", () => {
  expect(buildChatTimeline([message("a", 200), message("b", 200)], [run("z", 200), run("a", 200)]).map(item => item.id)).toEqual(["message:a", "message:b", "coding:a", "coding:z"]);
});
