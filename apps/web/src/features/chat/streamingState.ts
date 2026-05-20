import { parseAssistantContent } from "./assistantResponseParser";
import type {
  DoneEvent,
  ErrorEvent,
  IntermediateSteps,
  StreamEvent,
  StreamingMessage,
  ToolCallEntry,
} from "./streamingTypes";

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function patchLastToolCall(
  items: ToolCallEntry[],
  event: {
    tool_name: string;
    tool_call_id?: string;
    result_summary: string;
    success: boolean;
    timestamp: string;
  }
): ToolCallEntry[] {
  const list = [...items];
  let idx = -1;
  if (event.tool_call_id) {
    for (let index = list.length - 1; index >= 0; index -= 1) {
      if (list[index]?.id === event.tool_call_id) {
        idx = index;
        break;
      }
    }
  }
  if (idx < 0) {
    for (let index = list.length - 1; index >= 0; index -= 1) {
      if (list[index]?.tool_name === event.tool_name) {
        idx = index;
        break;
      }
    }
  }
  if (idx < 0) {
    return list;
  }

  const last = list[idx];
  if (!last || last.tool_name !== event.tool_name) {
    return list;
  }

  list[idx] = {
    ...last,
    result_summary: event.result_summary,
    success: event.success,
    completed_at: event.timestamp,
  };

  return list;
}

export function createEmptyIntermediateSteps(): IntermediateSteps {
  return {
    thinkingBlocks: [],
    toolCalls: [],
    executionStatuses: [],
  };
}

export function createInitialStreamingMessage(generateMessageId: () => string): StreamingMessage {
  return {
    id: generateMessageId(),
    role: "assistant",
    text: "",
    thinkingBlocks: [],
    toolCalls: [],
    executionStatuses: [],
    analysisPlan: null,
    checkpointDigest: null,
    workerResults: [],
    timestamp: new Date(),
    isStreaming: true,
    isComplete: false,
    isError: false,
  };
}

export function parseStreamEvent(data: string): StreamEvent | null {
  try {
    return JSON.parse(data) as StreamEvent;
  } catch {
    return null;
  }
}

export function extractSseData(rawEvent: string): string | null {
  const lines = rawEvent.replace(/\r/g, "").split("\n");
  const dataLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith(":")) {
      continue;
    }
    if (trimmed.startsWith("data:")) {
      dataLines.push(trimmed.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  return dataLines.join("\n");
}

export function createStreamErrorEvent(error: unknown): ErrorEvent {
  return {
    type: "error",
    code: "stream_error",
    message: error instanceof Error ? error.message : "Unknown error",
    timestamp: new Date().toISOString(),
  };
}

export function createCancelledMessage(current: StreamingMessage): StreamingMessage {
  return {
    ...current,
    isStreaming: false,
    isComplete: false,
    isError: false,
    text: current.text || "Generation cancelled",
  };
}

export function applyStreamEvent(
  previous: StreamingMessage,
  event: StreamEvent,
  steps: IntermediateSteps
): StreamingMessage {
  let updated: StreamingMessage = {
    ...previous,
    thinkingBlocks: [...previous.thinkingBlocks],
    toolCalls: [...previous.toolCalls],
    executionStatuses: [...previous.executionStatuses],
  };

  const pushThinkingBlock = (content: string, timestamp: string) => {
    const trimmed = content.trim();
    if (!trimmed) {
      return;
    }

    const alreadyExists =
      steps.thinkingBlocks[steps.thinkingBlocks.length - 1]?.content === trimmed;
    if (alreadyExists) {
      return;
    }

    const thinkingBlock = {
      id: createId("thinking"),
      content: trimmed,
      timestamp,
      sequence:
        steps.thinkingBlocks.length +
        steps.toolCalls.length +
        steps.executionStatuses.length,
    };

    updated = {
      ...updated,
      thinkingBlocks: [...updated.thinkingBlocks, thinkingBlock],
    };
    steps.thinkingBlocks = [...steps.thinkingBlocks, thinkingBlock];
  };

  switch (event.type) {
    case "thinking": {
      pushThinkingBlock(event.content, event.timestamp);
      break;
    }

    case "tool_call_start": {
      const toolCall = {
        id: event.tool_call_id ?? createId("tool"),
        tool_name: event.tool_name,
        arguments: event.arguments,
        started_at: event.timestamp,
        sequence:
          steps.thinkingBlocks.length +
          steps.toolCalls.length +
          steps.executionStatuses.length,
      };
      updated = {
        ...updated,
        toolCalls: [...updated.toolCalls, toolCall],
      };
      steps.toolCalls = [...steps.toolCalls, toolCall];
      break;
    }

    case "tool_call_end": {
      updated = {
        ...updated,
        toolCalls: patchLastToolCall(updated.toolCalls, event),
      };
      steps.toolCalls = patchLastToolCall(steps.toolCalls, event);
      break;
    }

    case "execution_status": {
      const execStatus = {
        status: event.status,
        message: event.message,
        timestamp: event.timestamp,
        sequence:
          steps.thinkingBlocks.length +
          steps.toolCalls.length +
          steps.executionStatuses.length,
      };
      updated = {
        ...updated,
        executionStatuses: [...updated.executionStatuses, execStatus],
      };
      steps.executionStatuses = [...steps.executionStatuses, execStatus];
      break;
    }

    case "runtime_state": {
      updated = {
        ...updated,
        analysisPlan: event.analysis_plan ?? updated.analysisPlan ?? null,
        checkpointDigest: event.checkpoint_digest ?? updated.checkpointDigest ?? null,
        workerResults: event.worker_results ?? updated.workerResults ?? [],
      };
      break;
    }

    case "chunk": {
      const parsedChunk = parseAssistantContent(event.content);
      for (const block of parsedChunk.thinkingBlocks) {
        pushThinkingBlock(block, event.timestamp);
      }
      if (parsedChunk.answer) {
        updated = {
          ...updated,
          text: updated.text + parsedChunk.answer,
        };
      }
      break;
    }

    case "done": {
      const parsedDone = parseAssistantContent(event.answer);
      for (const block of parsedDone.thinkingBlocks) {
        pushThinkingBlock(block, event.timestamp);
      }
      updated = {
        ...updated,
        payload: event,
        analysisPlan: event.analysis_plan ?? updated.analysisPlan ?? null,
        checkpointDigest: event.checkpoint_digest ?? updated.checkpointDigest ?? null,
        workerResults: event.worker_results ?? updated.workerResults ?? [],
        text: parsedDone.answer || updated.text || event.answer,
        isStreaming: false,
        isComplete: true,
        isError: false,
      };
      break;
    }

    case "error": {
      updated = {
        ...updated,
        isStreaming: false,
        isComplete: false,
        isError: true,
        text: updated.text || `Error: ${event.message}`,
      };
      break;
    }
  }

  return updated;
}

export function isTerminalEvent(event: StreamEvent): event is DoneEvent | ErrorEvent {
  return event.type === "done" || event.type === "error";
}
