import type { CodingRun } from "@/features/coding/codingApi";
import type { ChatMessageViewModel } from "./chatViewModel";

export type ChatTimelineItem =
  | { kind: "message"; id: string; message: ChatMessageViewModel }
  | { kind: "coding"; id: string; run: CodingRun };

/** A run owns its evolving activity at its creation time, never at the tail of the chat. */
export function buildChatTimeline(messages: ChatMessageViewModel[], runs: CodingRun[]): ChatTimelineItem[] {
  const orderedRuns = [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  const timeline: ChatTimelineItem[] = [];
  let runIndex = 0;
  for (const message of messages) {
    while (runIndex < orderedRuns.length && Date.parse(orderedRuns[runIndex].created_at) < message.timestamp.getTime()) {
      const run = orderedRuns[runIndex++];
      timeline.push({ kind: "coding", id: `coding:${run.id}`, run });
    }
    timeline.push({ kind: "message", id: `message:${message.id}`, message });
  }
  for (const run of orderedRuns.slice(runIndex)) timeline.push({ kind: "coding", id: `coding:${run.id}`, run });
  return timeline;
}
