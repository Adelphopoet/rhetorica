import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CollapsibleThinking } from "./streaming-components";

describe("CollapsibleThinking", () => {
  it("renders thinking blocks and tool calls in chronological order", () => {
    render(
      <CollapsibleThinking
        thinkingBlocks={[
          {
            id: "thinking-1000-a",
            content: "First thought",
            timestamp: "2026-04-04T14:30:48Z",
          },
          {
            id: "thinking-3000-b",
            content: "Second thought",
            timestamp: "2026-04-04T14:30:54Z",
          },
        ]}
        toolCalls={[
          {
            id: "tool-2000-a",
            tool_name: "search_metadata",
            started_at: "2026-04-04T14:30:50Z",
            completed_at: "2026-04-04T14:30:51Z",
            success: true,
            result_summary: "Found 20 items",
          },
        ]}
        executionStatuses={[]}
        defaultExpanded
      />
    );

    const firstThought = screen.getByText("First thought");
    const toolCall = screen.getByText("Search Metadata");
    const secondThought = screen.getByText("Second thought");

    expect(firstThought.compareDocumentPosition(toolCall) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolCall.compareDocumentPosition(secondThought) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("preserves interleaved event order when timestamps collapse to the same second", () => {
    render(
      <CollapsibleThinking
        thinkingBlocks={[
          {
            id: "thinking-a",
            content: "First thought",
            timestamp: "2026-04-04T14:30:48Z",
            sequence: 0,
          },
          {
            id: "thinking-b",
            content: "Second thought",
            timestamp: "2026-04-04T14:30:48Z",
            sequence: 2,
          },
        ]}
        toolCalls={[
          {
            id: "tool-a",
            tool_name: "fetch_metadata_tables",
            started_at: "2026-04-04T14:30:48Z",
            completed_at: "2026-04-04T14:30:48Z",
            success: true,
            result_summary: "Found 2 tables",
            sequence: 1,
          },
        ]}
        executionStatuses={[]}
        defaultExpanded
      />
    );

    const firstThought = screen.getByText("First thought");
    const toolCall = screen.getByText("Fetch Metadata Tables");
    const secondThought = screen.getByText("Second thought");

    expect(firstThought.compareDocumentPosition(toolCall) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolCall.compareDocumentPosition(secondThought) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
