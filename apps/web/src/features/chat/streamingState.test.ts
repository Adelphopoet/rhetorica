import { describe, expect, it } from "vitest";

import { applyStreamEvent, createEmptyIntermediateSteps, createInitialStreamingMessage } from "./streamingState";
import type { ChunkEvent, RuntimeStateEvent, ToolCallEndEvent, ToolCallStartEvent } from "./streamingTypes";

describe("streamingState", () => {
  it("preserves markdown line breaks across streamed chunks", () => {
    const steps = createEmptyIntermediateSteps();
    let message = createInitialStreamingMessage(() => "assistant-1");

    const firstChunk: ChunkEvent = {
      type: "chunk",
      content: "# Title\n",
      timestamp: "2026-04-04T12:52:00Z",
    };
    const secondChunk: ChunkEvent = {
      type: "chunk",
      content: "\n## Subtitle\n- item",
      timestamp: "2026-04-04T12:52:01Z",
    };

    message = applyStreamEvent(message, firstChunk, steps);
    message = applyStreamEvent(message, secondChunk, steps);

    expect(message.text).toBe("# Title\n\n## Subtitle\n- item");
  });

  it("matches nested tool completions by tool_call_id", () => {
    const steps = createEmptyIntermediateSteps();
    let message = createInitialStreamingMessage(() => "assistant-2");

    const runSqlStart: ToolCallStartEvent = {
      type: "tool_call_start",
      tool_name: "run_sql",
      tool_call_id: "call-run-sql",
      timestamp: "2026-04-04T12:52:00Z",
    };
    const optimizerStart: ToolCallStartEvent = {
      type: "tool_call_start",
      tool_name: "sql_optimization_advisor",
      tool_call_id: "call-optimizer",
      timestamp: "2026-04-04T12:52:01Z",
    };
    const optimizerEnd: ToolCallEndEvent = {
      type: "tool_call_end",
      tool_name: "sql_optimization_advisor",
      tool_call_id: "call-optimizer",
      result_summary: "Advisory only: 1 finding",
      success: true,
      timestamp: "2026-04-04T12:52:02Z",
    };
    const runSqlEnd: ToolCallEndEvent = {
      type: "tool_call_end",
      tool_name: "run_sql",
      tool_call_id: "call-run-sql",
      result_summary: "Success",
      success: true,
      timestamp: "2026-04-04T12:52:03Z",
    };

    message = applyStreamEvent(message, runSqlStart, steps);
    message = applyStreamEvent(message, optimizerStart, steps);
    message = applyStreamEvent(message, optimizerEnd, steps);
    message = applyStreamEvent(message, runSqlEnd, steps);

    expect(message.toolCalls).toHaveLength(2);
    expect(message.toolCalls[0]).toMatchObject({
      id: "call-run-sql",
      tool_name: "run_sql",
      result_summary: "Success",
      completed_at: "2026-04-04T12:52:03Z",
    });
    expect(message.toolCalls[1]).toMatchObject({
      id: "call-optimizer",
      tool_name: "sql_optimization_advisor",
      result_summary: "Advisory only: 1 finding",
      completed_at: "2026-04-04T12:52:02Z",
    });
  });

  it("stores live analysis plan runtime updates before done", () => {
    const steps = createEmptyIntermediateSteps();
    let message = createInitialStreamingMessage(() => "assistant-3");

    const runtimeState: RuntimeStateEvent = {
      type: "runtime_state",
      timestamp: "2026-04-04T12:52:00Z",
      analysis_plan: {
        plan_id: "plan-1",
        goal: "Explain GMV drop",
        status: "planned",
        steps: [
          {
            id: "baseline",
            title: "Build baseline",
            status: "planned",
            required_evidence: ["execution_result"],
            evidence_ids: [],
          },
        ],
        required_evidence: ["execution_result"],
        open_questions: [],
        warnings: [],
      },
      checkpoint_digest: {
        run_id: "run-1",
        plan_id: "plan-1",
        status: "planned",
        completed_step_ids: [],
        evidence_ids: [],
        warnings: [],
      },
      worker_results: [],
    };

    message = applyStreamEvent(message, runtimeState, steps);

    expect(message.analysisPlan?.steps[0]?.id).toBe("baseline");
    expect(message.checkpointDigest?.status).toBe("planned");
    expect(message.isStreaming).toBe(true);
  });
});
