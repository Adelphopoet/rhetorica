import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
            tool_name: "catalog__search",
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
    const toolCall = screen.getByText(/Catalog\s+Search/);
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
            tool_name: "catalog__search",
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
    const toolCall = screen.getByText("Catalog Search");
    const secondThought = screen.getByText("Second thought");

    expect(firstThought.compareDocumentPosition(toolCall) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolCall.compareDocumentPosition(secondThought) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders repeated provider tool call ids without duplicate React keys", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    try {
      render(
        <CollapsibleThinking
          thinkingBlocks={[]}
          toolCalls={[
            {
              id: "functions.mcp_confluence_main__confluence_search:7",
              tool_name: "catalog__search",
              started_at: "2026-08-13T18:30:00Z",
              sequence: 0,
            },
            {
              id: "functions.mcp_confluence_main__confluence_search:7",
              tool_name: "catalog__search",
              started_at: "2026-08-13T18:30:01Z",
              sequence: 1,
            },
          ]}
          executionStatuses={[]}
          defaultExpanded
        />
      );

      expect(screen.getAllByText(/Catalog\s+Search/)).toHaveLength(2);
      expect(
        consoleError.mock.calls.some((call) =>
          call.some((part) => String(part).includes("same key"))
        )
      ).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });
});

it("does not offer empty JSON arguments and shows missing result instead of endless activity", () => {
  render(<CollapsibleThinking defaultExpanded thinkingBlocks={[]} executionStatuses={[]} toolCalls={[{ id: "call", tool_name: "repository_run_command", arguments: {}, started_at: "2026-10-06T08:00:00Z" }]} />);
  expect(screen.queryByText("Аргументы:")).not.toBeInTheDocument();
  expect(screen.queryByText("Показать аргументы")).not.toBeInTheDocument();
  expect(screen.getByText("Нет результата")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Скрыть журнал выполнения" })).toHaveAttribute("aria-expanded", "true");
});

it("shows command arguments and a zero-second duration", () => {
  render(<CollapsibleThinking defaultExpanded thinkingBlocks={[]} executionStatuses={[]} toolCalls={[{ id: "call", tool_name: "repository_run_command", arguments: { argv: ["git", "status", "--short"] }, started_at: "2026-10-06T08:00:00Z", completed_at: "2026-10-06T08:00:00Z", success: true }]} />);
  expect(screen.getByText(/git status --short/)).toBeInTheDocument();
  expect(screen.getByText("0s")).toBeInTheDocument();
});
