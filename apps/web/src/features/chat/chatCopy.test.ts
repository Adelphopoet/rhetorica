import { describe, expect, it } from "vitest";

import type { ChatMessageViewModel } from "./chatViewModel";
import { serializeChatHistoryForCopy, serializeChatMessageForCopy } from "./chatCopy";

describe("chatCopy", () => {
  it("serializes a message with the full system run log", () => {
    const serialized = serializeChatMessageForCopy({
      role: "assistant",
      text: "Revenue is up 12%",
      timestamp: new Date("2026-04-04T09:00:00Z"),
      thinkingBlocks: [
        {
          id: "thinking-1",
          content: "Checking revenue sources",
          timestamp: "2026-04-04T09:00:01Z",
        },
      ],
      toolCalls: [
        {
          id: "tool-1",
          tool_name: "catalog__search",
          arguments: { entityType: "table", query: "revenue" },
          result_summary: "Found 2 tables",
          success: true,
          started_at: "2026-04-04T09:00:02Z",
          completed_at: "2026-04-04T09:00:03Z",
        },
      ],
      executionStatuses: [
        {
          status: "running",
          message: "Executing SQL",
          timestamp: "2026-04-04T09:00:04Z",
        },
      ],
    });

    expect(serialized).toContain("Rhetorica Assistant");
    expect(serialized).toContain("Revenue is up 12%");
    expect(serialized).toContain("Журнал выполнения:");
    expect(serialized).toContain("Checking revenue sources");
    expect(serialized).toContain("Вызов инструмента: catalog__search");
    expect(serialized).toContain("Статус выполнения: running");
  });

  it("does not synthesize removed optimizer events when copying historical chat", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-optimizer",
        role: "assistant",
        text: "Optimizer inspected the query",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: {
          answer: "Optimizer inspected the query",
          conversation_id: "conv-1",
          generated_sql: "select * from revenue",
          optimization_trace: {
            optimizer_used: true,
            optimizer_provider: "clickhouse_rule_advisor",
            input_sql: "select * from revenue",
            optimized_sql_draft: "select * from revenue final",
            input_sql_hash: "in",
            optimized_sql_hash: "out",
            accepted_or_rejected: "advisory_only",
            applied: false,
            risk_level: "medium",
            confidence: 0.82,
            engine_scope: "clickhouse",
            rules_triggered: ["query-join-filter-before"],
            findings: [
              {
                rule_id: "query-join-filter-before",
                title: "Push filters earlier",
                summary: "Filter before joining the large fact",
                severity: "warning",
                applies_to: ["dm.revenue"],
                citations: [],
              },
            ],
            recommended_actions: [],
            citations: [],
            warnings: [],
            skills_used: ["clickhouse-best-practices"],
            evidence_used: ["ddl"],
          },
          selected_database_connector: "clickhouse",
          assistant_message_id: "msg-1",
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
          cancelled: false,
        },
      },
    ];

    const serialized = serializeChatHistoryForCopy(messages, { userLabel: "Ada Lovelace" });

    expect(serialized).not.toContain("Вызов инструмента: sql_optimization_advisor");
    expect(serialized).toContain("Optimizer inspected the query");
  });

  it("uses the profile name for user messages when copying whole chat history", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "user-1",
        role: "user",
        text: "Why did GMV drop?",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ];

    const serialized = serializeChatHistoryForCopy(messages, { userLabel: "Ada Lovelace" });

    expect(serialized).toContain("Ada Lovelace");
    expect(serialized).not.toContain("Analyst");
    expect(serialized).toContain("Why did GMV drop?");
  });
});
