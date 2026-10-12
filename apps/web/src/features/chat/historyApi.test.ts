import { describe, expect, it, vi, beforeEach } from "vitest";

// Import the functions we need to test
import {
  cacheMessageIntermediateSteps,
  lazyLoadExecution,
  mergeHydratedMessages,
  loadConversationThread,
} from "@/features/chat/historyApi";
import type { ChatMessageViewModel } from "@/features/chat/chatViewModel";

// Mock fetch globally
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

function installTestLocalStorage() {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: vi.fn((key: string) => store.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        store.set(key, value);
      }),
      removeItem: vi.fn((key: string) => {
        store.delete(key);
      }),
      clear: vi.fn(() => {
        store.clear();
      }),
    },
  });
}

// Mock getExecutionDetail behavior
const mockExecutionDetails: Record<string, object> = {
  "exec-1": {
    history: {
      execution_id: "exec-1",
      status: "completed",
      engine: "duckdb",
      normalized_sql: "SELECT 1",
      row_count: 1,
      duration_ms: 100,
      warnings: [],
      error_message: null,
    },
    result: {
      execution_id: "exec-1",
      status: "completed",
      engine: "duckdb",
      normalized_sql: "SELECT 1",
      row_count: 1,
      duration_ms: 100,
      error_message: null,
      preview: {
        columns: [{ name: "1", type: "INTEGER" }],
        rows: [[1]],
      },
    },
  },
  "exec-2": {
    history: {
      execution_id: "exec-2",
      status: "completed",
      engine: "duckdb",
      normalized_sql: "SELECT 2",
      row_count: 1,
      duration_ms: 200,
      warnings: [],
      error_message: null,
    },
    result: {
      execution_id: "exec-2",
      status: "completed",
      engine: "duckdb",
      normalized_sql: "SELECT 2",
      row_count: 1,
      duration_ms: 200,
      error_message: null,
      preview: {
        columns: [{ name: "2", type: "INTEGER" }],
        rows: [[2]],
      },
    },
  },
};

describe("historyApi", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    installTestLocalStorage();
  });

  describe("lazyLoadExecution", () => {
    it("returns execution data when found", async () => {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(mockExecutionDetails["exec-1"]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await lazyLoadExecution("exec-1");

      expect(result).not.toBeNull();
      expect(result?.execution_id).toBe("exec-1");
      expect(result?.normalized_sql).toBe("SELECT 1");
      expect(result?.row_count).toBe(1);
    });

    it("returns null when execution not found (404)", async () => {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Execution not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await lazyLoadExecution("non-existent");

      expect(result).toBeNull();
    });

    it("returns minimal execution data from history when result is null", async () => {
      const detailWithoutResult = {
        history: {
          execution_id: "exec-minimal",
          status: "completed",
          engine: "duckdb",
          normalized_sql: "SELECT * FROM table",
          row_count: 100,
          duration_ms: 500,
          warnings: ["Test warning"],
          error_message: null,
        },
        result: null,
      };

      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(detailWithoutResult), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await lazyLoadExecution("exec-minimal");

      expect(result).not.toBeNull();
      expect(result?.execution_id).toBe("exec-minimal");
      expect(result?.row_count).toBe(100);
      expect(result?.preview.rows).toEqual([]);
    });
  });

  describe("mergeHydratedMessages", () => {
    it("merges hydrated messages with current messages", () => {
      const currentMessages: ChatMessageViewModel[] = [
        {
          id: "local-1",
          backendMessageId: "db-1",
          role: "assistant",
          text: "Streaming response",
          timestamp: new Date(),
          deliveryState: "complete",
          payload: {
            answer: "Streaming",
            conversation_id: "conv-1",
            generated_sql: null,
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "db-1",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
          thinkingBlocks: [{ id: "tb-1", content: "Thinking...", timestamp: "2026-05-05T00:00:00Z" }],
          toolCalls: [],
          executionStatuses: [],
        },
      ];

      const hydratedMessages: ChatMessageViewModel[] = [
        {
          id: "db-1",
          backendMessageId: "db-1",
          role: "assistant",
          text: "DB response",
          timestamp: new Date(),
          deliveryState: "complete",
          payload: {
            answer: "Final answer",
            conversation_id: "conv-1",
            generated_sql: "SELECT * FROM table",
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: "warehouse",
            assistant_message_id: "db-1",
            metadata_sources: ["Table 1"],
            context_tables: [],
            claims: [{ claim_id: "c1", text: "Result", status: "verified" as const }],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: {
              execution_id: "exec-1",
              status: "completed",
              engine: "duckdb",
              normalized_sql: "SELECT * FROM table",
              row_count: 10,
              duration_ms: 100,
              error_message: null,
              preview: { columns: [], rows: [] },
            },
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
          thinkingBlocks: [],
          toolCalls: [],
          executionStatuses: [],
        },
      ];

      const result = mergeHydratedMessages(currentMessages, hydratedMessages);

      // Should preserve local id
      expect(result[0].id).toBe("local-1");
      // Should use hydrated payload
      expect(result[0].payload?.generated_sql).toBe("SELECT * FROM table");
      expect(result[0].payload?.execution).toBeDefined();
      // Should preserve streaming thinking blocks
      expect(result[0].thinkingBlocks).toEqual([{ id: "tb-1", content: "Thinking...", timestamp: "2026-05-05T00:00:00Z" }]);
    });

    it("returns hydrated messages unchanged when no matching current messages", () => {
      const currentMessages: ChatMessageViewModel[] = [];
      const hydratedMessages: ChatMessageViewModel[] = [
        {
          id: "db-1",
          backendMessageId: "db-1",
          role: "assistant",
          text: "Response",
          timestamp: new Date(),
          deliveryState: "complete",
          payload: {
            answer: "Answer",
            conversation_id: "conv-1",
            generated_sql: null,
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "db-1",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
          thinkingBlocks: [],
          toolCalls: [],
          executionStatuses: [],
        },
      ];

      const result = mergeHydratedMessages(currentMessages, hydratedMessages);

      expect(result).toEqual(hydratedMessages);
    });

    it("returns hydrated messages with partial matches", () => {
      const currentMessages: ChatMessageViewModel[] = [
        {
          id: "local-2",
          backendMessageId: "db-2",
          role: "assistant",
          text: "Local text",
          timestamp: new Date(),
          deliveryState: "streaming",
          thinkingBlocks: [{ id: "tb-1", content: "Thinking...", timestamp: "2026-05-05T00:00:00Z" }],
          toolCalls: [{ id: "tc-1", tool_name: "tool", arguments: {} }],
          executionStatuses: [{ status: "running" as const, message: "Running...", timestamp: "2026-05-05T00:00:00Z" }],
          payload: {
            answer: "Local",
            conversation_id: "conv-1",
            generated_sql: null,
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "db-2",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
        },
      ];

      const hydratedMessages: ChatMessageViewModel[] = [
        {
          id: "db-1",
          backendMessageId: "db-1",
          role: "assistant",
          text: "Message 1",
          timestamp: new Date(),
          deliveryState: "complete",
          thinkingBlocks: [],
          toolCalls: [],
          executionStatuses: [],
          payload: {
            answer: "Answer 1",
            conversation_id: "conv-1",
            generated_sql: null,
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "db-1",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
        },
        {
          id: "db-2",
          backendMessageId: "db-2",
          role: "assistant",
          text: "Message 2",
          timestamp: new Date(),
          deliveryState: "complete",
          thinkingBlocks: [],
          toolCalls: [],
          executionStatuses: [],
          payload: {
            answer: "Answer 2",
            conversation_id: "conv-1",
            generated_sql: "SELECT 2",
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "db-2",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
        },
      ];

      const result = mergeHydratedMessages(currentMessages, hydratedMessages);

      // db-1: no match, returns as-is
      expect(result[0].id).toBe("db-1");
      expect(result[0].text).toBe("Message 1");

      // db-2: matches local, preserves id and streaming data
      expect(result[1].id).toBe("local-2");
      expect(result[1].text).toBe("Message 2");
      expect(result[1].deliveryState).toBe("complete"); // DB is complete, takes precedence
      expect(result[1].thinkingBlocks).toEqual([{ id: "tb-1", content: "Thinking...", timestamp: "2026-05-05T00:00:00Z" }]);
      expect(result[1].toolCalls).toEqual([{ id: "tc-1", tool_name: "tool", arguments: {} }]);
      expect(result[1].executionStatuses).toEqual([{ status: "running" as const, message: "Running...", timestamp: "2026-05-05T00:00:00Z" }]);
      // merged payload: uses hydrated generated_sql but current's other fields
      expect(result[1].payload?.generated_sql).toBe("SELECT 2");
      expect(result[1].payload?.answer).toBe("Local"); // From current
    });

    it("returns messages without backendMessageId unchanged", () => {
      const currentMessages: ChatMessageViewModel[] = [
        {
          id: "local-1",
          // No backendMessageId
          role: "assistant",
          text: "No backend",
          timestamp: new Date(),
          deliveryState: "complete",
          payload: {
            answer: "Answer",
            conversation_id: "conv-1",
            generated_sql: null,
            optimization_trace: null,
            capability_trace: null,
            selected_database_connector: null,
            assistant_message_id: "local-1",
            metadata_sources: [],
            context_tables: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            execution: null,
            analysis_document: null,
            visualization: null,
            warnings: [],
          },
        },
      ];

      const hydratedMessages: ChatMessageViewModel[] = [];

      const result = mergeHydratedMessages(currentMessages, hydratedMessages);

      expect(result).toEqual(hydratedMessages);
    });
  });

  describe("loadConversationThread", () => {
    it("hydrates thinking blocks from persisted assistant content", async () => {
      const conversationDetail = {
        id: "conv-thinking",
        workspace_id: "default",
        title: "Thinking analysis",
        created_at: "2026-04-04T10:00:00Z",
        updated_at: "2026-04-04T10:00:00Z",
        messages: [
          {
            id: "msg-user-1",
            conversation_id: "conv-thinking",
            role: "user",
            content: "Question",
            created_at: "2026-04-04T10:00:00Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-assistant-1",
            conversation_id: "conv-thinking",
            role: "assistant",
            content: "<thinking>Checking metadata first.</thinking>\nFinal answer",
            created_at: "2026-04-04T10:00:05Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: "wrong",
            feedback_updated_at: "2026-04-04T10:01:00Z",
            feedback_reason: "wrong_metric",
            feedback_comment: "The comparison baseline is wrong.",
            feedback_what_went_wrong: "The answer compares different time periods.",
            feedback_expected_answer: "Compare the same period year over year.",
          },
        ],
      };

      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(conversationDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await loadConversationThread("conv-thinking");

      expect(result.messages[1].text).toBe("Final answer");
      expect(result.messages[1].payload?.answer).toBe("Final answer");
      expect(result.messages[1]).toMatchObject({
        feedback: "wrong",
        feedbackReason: "wrong_metric",
        feedbackComment: "The comparison baseline is wrong.",
        feedbackWhatWentWrong: "The answer compares different time periods.",
        feedbackExpectedAnswer: "Compare the same period year over year.",
      });
      expect(result.messages[1].thinkingBlocks).toEqual([
        {
          id: "thinking-msg-assistant-1-0",
          content: "Checking metadata first.",
          timestamp: "2026-04-04T10:00:05Z",
          sequence: 0,
        },
      ]);
    });

    it("hydrates cached live system log after page reload", async () => {
      const conversationDetail = {
        id: "conv-cached-log",
        workspace_id: "default",
        title: "Cached log",
        created_at: "2026-04-04T10:00:00Z",
        updated_at: "2026-04-04T10:00:00Z",
        messages: [
          {
            id: "msg-assistant-1",
            conversation_id: "conv-cached-log",
            role: "assistant",
            content: "Final answer",
            created_at: "2026-04-04T10:00:05Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
        ],
      };

      cacheMessageIntermediateSteps("msg-assistant-1", {
        thinkingBlocks: [
          {
            id: "thinking-live-1",
            content: "Live thinking block",
            timestamp: "2026-04-04T10:00:04Z",
            sequence: 0,
          },
        ],
        toolCalls: [
          {
            id: "tool-live-1",
            tool_name: "catalog__search",
            started_at: "2026-04-04T10:00:04Z",
            sequence: 1,
          },
        ],
        executionStatuses: [],
      });

      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(conversationDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await loadConversationThread("conv-cached-log");

      expect(result.messages[0].thinkingBlocks).toEqual([
        {
          id: "thinking-live-1",
          content: "Live thinking block",
          timestamp: "2026-04-04T10:00:04Z",
          sequence: 0,
        },
      ]);
      expect(result.messages[0].toolCalls).toEqual([
        {
          id: "tool-live-1",
          tool_name: "catalog__search",
          started_at: "2026-04-04T10:00:04Z",
          sequence: 1,
        },
      ]);
    });

    it("returns assistantExecutionIds for all assistant messages with execution_id", async () => {
      const conversationDetail = {
        id: "conv-multi",
        workspace_id: "default",
        title: "Multi-exec analysis",
        created_at: "2026-04-04T10:00:00Z",
        updated_at: "2026-04-04T10:10:00Z",
        messages: [
          {
            id: "msg-user-1",
            conversation_id: "conv-multi",
            role: "user",
            content: "First question",
            created_at: "2026-04-04T10:00:00Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-assistant-1",
            conversation_id: "conv-multi",
            role: "assistant",
            content: "First answer",
            created_at: "2026-04-04T10:00:05Z",
            generated_sql: "SELECT 1",
            execution_id: "exec-1",
            context_tables: [],
            metadata_sources: [],
            claims: [{ claim_id: "c1", text: "First claim", status: "verified" as const }],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: "warehouse",
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-user-2",
            conversation_id: "conv-multi",
            role: "user",
            content: "Second question",
            created_at: "2026-04-04T10:05:00Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-assistant-2",
            conversation_id: "conv-multi",
            role: "assistant",
            content: "Second answer",
            created_at: "2026-04-04T10:05:05Z",
            generated_sql: "SELECT 2",
            execution_id: "exec-2",
            context_tables: [],
            metadata_sources: [],
            claims: [{ claim_id: "c2", text: "Second claim", status: "verified" as const }],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: "warehouse",
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
        ],
      };

      // Mock for conversation detail
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(conversationDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      // Mock for latest execution (exec-2)
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(mockExecutionDetails["exec-2"]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await loadConversationThread("conv-multi");

      // Should return both execution IDs
      expect(result.assistantExecutionIds).toEqual([
        { messageId: "msg-assistant-1", executionId: "exec-1" },
        { messageId: "msg-assistant-2", executionId: "exec-2" },
      ]);

      // Only the latest message (msg-assistant-2) should have execution data hydrated
      expect(result.messages[1].payload?.execution).toBeNull(); // msg-assistant-1
      expect(result.messages[3].payload?.execution?.execution_id).toBe("exec-2"); // msg-assistant-2
    });

    it("keeps conversation messages when execution enrichment is forbidden", async () => {
      const conversationDetail = {
        id: "conv-protected-exec",
        workspace_id: "default",
        title: "Protected execution",
        created_at: "2026-04-04T10:00:00Z",
        updated_at: "2026-04-04T10:00:05Z",
        messages: [
          {
            id: "msg-user-1",
            conversation_id: "conv-protected-exec",
            role: "user",
            content: "Question",
            created_at: "2026-04-04T10:00:00Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-assistant-1",
            conversation_id: "conv-protected-exec",
            role: "assistant",
            content: "Saved answer",
            created_at: "2026-04-04T10:00:05Z",
            generated_sql: "SELECT 1",
            execution_id: "exec-protected",
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: "warehouse",
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
        ],
      };
      fetchMock
        .mockResolvedValueOnce(
          new Response(JSON.stringify(conversationDetail), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        )
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ detail: "Access denied" }), {
            status: 403,
            headers: { "Content-Type": "application/json" },
          })
        );
      const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

      const result = await loadConversationThread("conv-protected-exec");

      expect(result.messages).toHaveLength(2);
      expect(result.messages[1].text).toBe("Saved answer");
      expect(result.messages[1].payload?.execution).toBeNull();
      expect(result.assistantExecutionIds).toEqual([
        { messageId: "msg-assistant-1", executionId: "exec-protected" },
      ]);
      expect(warn).toHaveBeenCalledWith(
        "Conversation execution enrichment failed",
        expect.any(Error)
      );
      warn.mockRestore();
    });

    it("returns empty assistantExecutionIds when no assistant messages have execution_id", async () => {
      const conversationDetail = {
        id: "conv-simple",
        workspace_id: "default",
        title: "Simple analysis",
        created_at: "2026-04-04T10:00:00Z",
        updated_at: "2026-04-04T10:00:00Z",
        messages: [
          {
            id: "msg-user-1",
            conversation_id: "conv-simple",
            role: "user",
            content: "Question",
            created_at: "2026-04-04T10:00:00Z",
            generated_sql: null,
            execution_id: null,
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
          {
            id: "msg-assistant-1",
            conversation_id: "conv-simple",
            role: "assistant",
            content: "Answer without execution",
            created_at: "2026-04-04T10:00:05Z",
            generated_sql: null,
            execution_id: null, // No execution
            context_tables: [],
            metadata_sources: [],
            claims: [],
            evidence: [],
            claim_evidence_links: [],
            uncertainty_flags: [],
            selected_connector: null,
            warnings: [],
            feedback: null,
            feedback_updated_at: null,
          },
        ],
      };

      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify(conversationDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

      const result = await loadConversationThread("conv-simple");

      expect(result.assistantExecutionIds).toEqual([]);
    });
  });
});
