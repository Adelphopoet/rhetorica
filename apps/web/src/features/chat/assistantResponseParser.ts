export interface ParsedAssistantContent {
  answer: string;
  thinkingBlocks: string[];
}

const THINKING_TAG_REGEX = /<(think|thinking)>([\s\S]*?)<\/\1>/gi;
const TOOL_CALL_TAG_REGEX = /<tool_call>[\s\S]*?<\/tool_call>/gi;
const MARKDOWN_JSON_FENCE_REGEX = /^```(?:json)?\s*([\s\S]*?)\s*```$/i;

function stripMarkdownJsonFence(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(MARKDOWN_JSON_FENCE_REGEX);
  return match?.[1]?.trim() ?? trimmed;
}

function extractJsonObject(value: string): Record<string, unknown> | null {
  const normalized = stripMarkdownJsonFence(value);

  try {
    const parsed = JSON.parse(normalized);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    const firstBrace = normalized.indexOf("{");
    const lastBrace = normalized.lastIndexOf("}");
    if (firstBrace === -1 || lastBrace <= firstBrace) {
      return null;
    }

    try {
      const parsed = JSON.parse(normalized.slice(firstBrace, lastBrace + 1));
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
}

export function parseAssistantContent(content: string): ParsedAssistantContent {
  const thinkingBlocks: string[] = [];

  const withoutThinking = content.replace(THINKING_TAG_REGEX, (_, __, inner: string) => {
    const trimmed = inner.trim();
    if (trimmed) {
      thinkingBlocks.push(trimmed);
    }
    return " ";
  });

  const normalized = withoutThinking.replace(TOOL_CALL_TAG_REGEX, " ");
  const parsed = extractJsonObject(normalized);
  const answerFromJson = parsed?.answer_markdown ?? parsed?.answer;

  if (typeof answerFromJson === "string" && answerFromJson.trim()) {
    return { answer: answerFromJson.trim(), thinkingBlocks };
  }

  return { answer: normalized, thinkingBlocks };
}
