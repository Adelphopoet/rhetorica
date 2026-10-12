import { ToastProvider } from "@rhetorica/ui";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatAskResponse, ChatMessageViewModel } from "@/features/chat/chatViewModel";
import { ChatThread } from "./chat-thread";

vi.mock("@/features/charts", () => ({
  VisualizationChart: ({ spec }: { spec?: { title?: string | null } | null }) => (
    <div data-testid="chart">{spec?.title ?? "Chart"}</div>
  ),
}));

function renderThread(
  messages: ChatMessageViewModel[],
  userLabel = "Ada Lovelace",
  overrides: Partial<Parameters<typeof ChatThread>[0]> = {}
) {
  return render(
    <ToastProvider>
      <ChatThread
        chatWidth={1100}
        conversationId="conversation-test"
        messages={messages}
        userLabel={userLabel}
        loading={false}
        feedbackPendingByMessageId={{}}
        onFeedback={() => undefined}
        onOpenScenarioCatalog={() => undefined}
        {...overrides}
      />
    </ToastProvider>
  );
}

const reportPayload: ChatAskResponse = {
  answer: "GMV recovered on paid traffic",
  conversation_id: "conv-report",
  generated_sql: "select day, gmv from dm.gmv_daily",
  optimization_trace: null,
  selected_database_connector: "clickhouse",
  assistant_message_id: "msg-report",
  metadata_sources: ["mcp"],
  context_tables: ["dm.gmv_daily"],
  claims: [],
  evidence: [],
  claim_evidence_links: [],
  uncertainty_flags: [],
  execution: {
    execution_id: "exec-report",
    status: "done",
    engine: "clickhouse",
    normalized_sql: "select day, gmv from dm.gmv_daily",
    row_count: 2,
    duration_ms: 44,
    error_message: null,
    preview: {
      columns: ["day", "gmv"],
      rows: [
        ["2026-04-01", 120],
        ["2026-04-02", 148],
      ],
    },
  },
  analysis_document: {
    document_version: "v1",
    title: "Weekly GMV recovery",
    executive_summary: "GMV is up 23% after paid traffic recovered",
    blocks: [
      {
        kind: "metric_grid",
        title: "KPI snapshot",
        description: "Core movement for the selected period",
        items: [
          {
            label: "GMV",
            value: "$148K",
            delta: "+23%",
            context: "vs previous comparable period",
            tone: "positive",
          },
          {
            label: "Orders",
            value: "9.8K",
            delta: "+8%",
            context: "stable conversion",
            tone: "neutral",
          },
        ],
      },
      {
        kind: "narrative_note",
        title: null,
        markdown: "---",
      },
      {
        kind: "section_header",
        title: "FT_ORDER_ART_SBER (СБЕРМАРКЕТ)",
        description: null,
      },
      {
        kind: "chart",
        title: "GMV trend",
        description: "Daily GMV by event date",
        insight: "Recovery starts after April 1",
        source_execution_id: "exec-report",
        preview: {
          columns: ["day", "gmv"],
          rows: [
            ["2026-04-01", 120],
            ["2026-04-02", 148],
          ],
        },
        visualization: {
          version: "v1",
          family: "temporal",
          kind: "line",
          goal: "trend",
          title: "GMV trend",
          description: "Daily GMV by event date",
          summary: "Recovery starts after April 1",
          planner_mode: "rule_fallback",
          confidence_score: 0.92,
          chart_safe_on_preview: true,
          x_axis_label: null,
          y_axis_label: null,
          value_format: "currency",
          metric_unit: null,
          data_source: {
            type: "execution_preview",
            execution_id: "exec-report",
            row_count: 2,
            is_truncated: false,
          },
          encoding: {
            x: "day",
            y: ["gmv"],
            series: null,
            color: null,
            size: null,
            facet: null,
            label: null,
            tooltip: [],
            stack: null,
            secondary_y: [],
          },
          transform: {
            sort: null,
            limit: 500,
            aggregation: "none",
            top_n: null,
            bin_size: null,
            window: null,
            pivot: null,
            normalize: "none",
            cumulative: false,
          },
          annotations: [],
          rationale: {
            why_kind: "A line chart fits daily movement",
            why_fields: ["day", "gmv"],
          },
          warnings: [],
        },
      },
      {
        kind: "finding",
        title: "Paid channel recovered",
        body: "The main lift comes from paid traffic cohorts",
        emphasis: "important",
      },
      {
        kind: "table",
        title: "Preview rows",
        description: "Raw preview remains available for drilldown",
        source: "execution_preview",
        source_execution_id: "exec-report",
        columns: ["day", "gmv"],
        preferred_hidden: true,
        action_label: "Show rows",
      },
      {
        kind: "caveat",
        title: "Attribution lag",
        body: "Late conversions can still adjust the final daily totals",
      },
      {
        kind: "caveat",
        title: "Warnings",
        body: "llm_finalizer_requested; explainability_synthesized; llm_unacceptable_final_payload; llm_finalizer_failed; preview_truncated",
      },
    ],
    warnings: [],
    provenance: {
      source_type: "chat_answer",
      execution_id: "exec-report",
      planner_mode: "rule_fallback",
    },
  },
  visualization: null,
  warnings: [],
  cancelled: false,
};

describe("ChatThread", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn(async () => undefined),
      },
    });
  });

  it("renders the simplified empty state", () => {
    renderThread([]);

    expect(screen.getByText(/разберите просадку метрики/i)).toBeInTheDocument();
    expect(screen.getByText(/прозрачный sql/i)).toBeInTheDocument();
  });

  it("shows a thread placeholder instead of the empty state while a saved chat loads", () => {
    renderThread([], "Ada Lovelace", { threadLoading: true });

    expect(screen.getByTestId("chat-thread-skeleton")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-empty-state-card")).not.toBeInTheDocument();
    expect(screen.queryByText(/разберите просадку метрики/i)).not.toBeInTheDocument();
  });

  it("falls back to the empty state when a loaded saved chat has no messages", () => {
    const view = renderThread([], "Ada Lovelace", { threadLoading: true });
    expect(screen.getByTestId("chat-thread-skeleton")).toBeInTheDocument();

    view.rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1100}
          conversationId="conversation-test"
          messages={[]}
          userLabel="Ada Lovelace"
          loading={false}
          threadLoading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>
    );

    expect(screen.queryByTestId("chat-thread-skeleton")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-empty-state-card")).toBeInTheDocument();
  });

  it("keeps message cards compactly spaced", () => {
    renderThread([
      {
        id: "user-spacing",
        role: "user",
        text: "Question",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
      {
        id: "assistant-spacing",
        role: "assistant",
        text: "Answer",
        timestamp: new Date("2026-04-04T09:00:01Z"),
        deliveryState: "complete",
      },
    ]);

    expect(screen.getByTestId("chat-message-user").parentElement).toHaveClass("gap-6");
  });

  it("applies the selected chat width to the empty state", () => {
    const { rerender } = renderThread([]);
    const card = screen.getByTestId("chat-empty-state-card");

    expect(card).toHaveClass("max-w-[1100px]");

    rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1600}
          conversationId="conversation-test"
          messages={[]}
          userLabel="Ada Lovelace"
          loading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>
    );
    expect(card).toHaveClass("max-w-[1600px]");
    expect(card).not.toHaveClass("max-w-[1100px]");
  });

  it("renders message lifecycle badges for assistant responses", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-1",
        role: "assistant",
        text: "Working on it",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "streaming",
        executionStatuses: [
          {
            status: "running",
            timestamp: "2026-04-04T09:00:01Z",
          },
        ],
      },
    ];

    renderThread(messages);

    expect(screen.getByTestId("chat-message-assistant")).toHaveClass(
      "border",
      "border-border",
      "ring-2",
      "ring-accent/30"
    );
    expect(screen.getByText(/выполняется sql/i)).toBeInTheDocument();
    expect(screen.getByText("Working on it")).toBeInTheDocument();
  });

  it("renders completed assistant responses inside a bordered surface", () => {
    renderThread([
      {
        id: "assistant-complete",
        role: "assistant",
        text: "Done",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ]);

    expect(screen.getByTestId("chat-message-assistant")).toHaveClass(
      "border",
      "border-border",
      "px-5",
      "py-4"
    );
  });

  it("does not restart auto-scroll when only the parent render changes", () => {
    const message: ChatMessageViewModel = {
      id: "assistant-stable",
      role: "assistant",
      text: "Completed answer",
      timestamp: new Date("2026-04-04T09:00:00Z"),
      deliveryState: "complete",
    };
    const scrollIntoViewSpy = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const view = renderThread([message]);
    scrollIntoViewSpy.mockClear();

    view.rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1100}
          conversationId="conversation-test"
          messages={[message]}
          userLabel="Ada Lovelace"
          loading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>
    );

    expect(scrollIntoViewSpy).not.toHaveBeenCalled();
    scrollIntoViewSpy.mockRestore();
  });

  it("follows streaming content only while the user remains near the bottom", () => {
    const message: ChatMessageViewModel = {
      id: "assistant-streaming",
      role: "assistant",
      text: "First chunk",
      timestamp: new Date("2026-04-04T09:00:00Z"),
      deliveryState: "streaming",
    };
    const view = renderThread([message]);
    const thread = view.container.querySelector<HTMLElement>(".chat-thread");
    expect(thread).not.toBeNull();
    if (!thread) {
      return;
    }

    let scrollHeight = 1000;
    Object.defineProperty(thread, "scrollHeight", {
      configurable: true,
      get: () => scrollHeight,
    });
    Object.defineProperty(thread, "clientHeight", {
      configurable: true,
      value: 200,
    });
    thread.scrollTop = 800;
    fireEvent.scroll(thread);

    scrollHeight = 1200;
    const secondChunk = { ...message, text: "First chunk and second chunk" };
    view.rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1100}
          conversationId="conversation-test"
          messages={[secondChunk]}
          loading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>
    );
    expect(thread.scrollTop).toBe(1200);

    thread.scrollTop = 400;
    fireEvent.scroll(thread);
    scrollHeight = 1400;
    view.rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1100}
          conversationId="conversation-test"
          messages={[{ ...secondChunk, text: "Third chunk" }]}
          loading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>
    );
    expect(thread.scrollTop).toBe(400);
  });

  it("resets sticky scroll when the conversation changes", () => {
    const firstMessage: ChatMessageViewModel = {
      id: "assistant-a",
      role: "assistant",
      text: "Conversation A",
      timestamp: new Date("2026-04-04T09:00:00Z"),
      deliveryState: "complete",
    };
    const secondMessage: ChatMessageViewModel = {
      ...firstMessage,
      id: "assistant-b",
      text: "Conversation B",
    };
    const view = renderThread([firstMessage], "Ada Lovelace", {
      conversationId: "conversation-a",
    });
    const thread = view.container.querySelector<HTMLElement>(".chat-thread");
    expect(thread).not.toBeNull();
    if (!thread) {
      return;
    }

    let scrollHeight = 1000;
    Object.defineProperty(thread, "scrollHeight", {
      configurable: true,
      get: () => scrollHeight,
    });
    Object.defineProperty(thread, "clientHeight", {
      configurable: true,
      value: 200,
    });
    thread.scrollTop = 300;
    fireEvent.scroll(thread);

    scrollHeight = 1200;
    view.rerender(
      <ToastProvider>
        <ChatThread
          chatWidth={1100}
          conversationId="conversation-b"
          messages={[secondMessage]}
          userLabel="Ada Lovelace"
          loading={false}
          feedbackPendingByMessageId={{}}
          onFeedback={() => undefined}
          onOpenScenarioCatalog={() => undefined}
        />
      </ToastProvider>,
    );

    expect(thread.scrollTop).toBe(1200);
  });

  it("renders the user profile name instead of the generic analyst label", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "user-1",
        role: "user",
        text: "Show weekly GMV",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ];

    renderThread(messages, "Ada Lovelace");

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.queryByText("Analyst")).not.toBeInTheDocument();
  });

  it("renders assistant markdown with rich formatting and safe links", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-markdown",
        role: "assistant",
        text: [
          "## Weekly GMV",
          "",
          "- **GMV** up 12%",
          "- `orders_count` is stable",
          "",
          "| Week | GMV |",
          "| --- | --- |",
          "| 2026-W13 | 120 |",
          "",
          "[Open report](https://example.com/report)",
        ].join("\n"),
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ];

    const { container } = renderThread(messages);

    expect(screen.getByRole("heading", { name: /weekly gmv/i })).toBeInTheDocument();
    expect(screen.getByText("GMV", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("orders_count", { selector: "code" })).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /open report/i });
    expect(link).toHaveAttribute("href", "https://example.com/report");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");

    expect(container.querySelector("table")).toBeInTheDocument();
  });

  it("does not offer report exports for plain persisted assistant answers", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-plain",
        backendMessageId: "msg-plain",
        role: "assistant",
        text: "Plain answer with no report",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: {
          ...reportPayload,
          assistant_message_id: "msg-plain",
          analysis_document: null,
        },
      },
    ];

    renderThread(messages);

    expect(screen.queryByRole("button", { name: /экспорт отчёта/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export html/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export pdf/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export ipynb/i })).not.toBeInTheDocument();
  });

  it("renders analysis_document as the primary assistant report and keeps raw rows collapsed", async () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-report",
        backendMessageId: "msg-report",
        role: "assistant",
        text: "GMV recovered on paid traffic",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: reportPayload,
      },
    ];

    renderThread(messages);

    expect(screen.queryByText(/^report$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /weekly gmv recovery/i })).not.toBeInTheDocument();
    expect(screen.getByText(/gmv is up 23%/i)).toBeInTheDocument();
    expect(screen.getByText("KPI snapshot")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /ft_order_art_sber/i })).toBeInTheDocument();
    expect(screen.getByText("$148K")).toBeInTheDocument();
    expect(screen.queryByText(/analyst note/i)).not.toBeInTheDocument();
    expect(await screen.findByTestId("chart")).toHaveTextContent("GMV trend");
    expect(screen.getByText(/paid channel recovered/i)).toBeInTheDocument();
    expect(screen.getByText(/attribution lag/i)).toBeInTheDocument();
    expect(screen.queryByText(/^warnings$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/llm_finalizer_requested/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /экспорт отчёта/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /html/i })).not.toBeInTheDocument();
    expect(screen.getByText("SQL", { selector: "summary" })).toBeInTheDocument();
    expect(screen.getByText("select day, gmv from dm.gmv_daily")).toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "2026-04-01" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /экспорт отчёта/i }));

    expect(screen.getByRole("menuitem", { name: /html/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /pdf/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /ipynb/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /show rows/i }));

    expect(await screen.findByRole("cell", { name: "2026-04-01" })).toBeInTheDocument();
  });

  it("renders standalone visualization payloads in assistant messages", async () => {
    const chartBlock = reportPayload.analysis_document?.blocks.find(
      (block) => block.kind === "chart"
    );
    if (!chartBlock || chartBlock.kind !== "chart") {
      throw new Error("chart fixture is missing");
    }

    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-chart",
        backendMessageId: "msg-chart",
        role: "assistant",
        text: "Количество заказов по дням",
        timestamp: new Date("2026-05-08T14:02:09Z"),
        deliveryState: "complete",
        payload: {
          ...reportPayload,
          analysis_document: null,
          visualization: {
            ...chartBlock.visualization,
            title: "Количество заказов по дням, май 2026",
          },
        },
      },
    ];

    renderThread(messages);

    expect(await screen.findByTestId("chart")).toHaveTextContent(
      "Количество заказов по дням, май 2026"
    );
    expect(screen.getAllByText(/количество заказов по дням/i).length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: /экспорт отчёта/i })).toBeInTheDocument();
  });

  it.each([
    ["mobile", 390],
    ["desktop", 1440],
  ])("keeps the inline report usable in a %s viewport", async (_label, width) => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: width,
    });

    const { container } = renderThread([
      {
        id: `assistant-report-${width}`,
        backendMessageId: `msg-report-${width}`,
        role: "assistant",
        text: "GMV recovered on paid traffic",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: reportPayload,
      },
    ]);

    expect(await screen.findByTestId("chart")).toBeInTheDocument();
    expect(container.querySelector(".analysis-report")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open result inspector/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "2026-04-02" })).not.toBeInTheDocument();
  });

  it("sanitizes assistant markdown html payloads", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-sanitized",
        role: "assistant",
        text: '<img src="x" onerror="alert(1)" /><script>alert(1)</script>safe',
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ];

    const { container } = renderThread(messages);

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("img")?.getAttribute("onerror")).toBeNull();
    expect(screen.getByText("safe")).toBeInTheDocument();
  });

  it("does not synthesize an optimizer tool card from a historical trace", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-optimizer",
        role: "assistant",
        text: "Оптимизировал запрос",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: {
          answer: "Оптимизировал запрос",
          conversation_id: "conv-opt",
          generated_sql: "select * from t where x < 3",
          optimization_trace: {
            optimizer_used: true,
            optimizer_provider: "clickhouse_rule_advisor",
            input_sql: "select * from t where x < 3",
            optimized_sql_draft: "select * from (select * from t where x < 3) as t",
            input_sql_hash: "in",
            optimized_sql_hash: "out",
            accepted_or_rejected: "advisory_only",
            applied: false,
            risk_level: "medium",
            confidence: 0.74,
            engine_scope: "clickhouse",
            rules_triggered: ["query-join-filter-before", "query-join-consider-alternatives"],
            findings: [
              {
                rule_id: "query-join-filter-before",
                title: "Date filter can be mirrored before the JOIN",
                summary: "Mirror the business-date filter on both joined facts to shrink scans",
                severity: "warning",
                applies_to: ["dm.ft_order", "dm.ft_order_unit_economics_v"],
                citations: [],
              },
            ],
            recommended_actions: [],
            citations: [],
            warnings: [],
            skills_used: ["clickhouse-best-practices"],
            evidence_used: ["ddl", "explain:indexes=1"],
          },
          selected_database_connector: "clickhouse",
          assistant_message_id: "msg-opt",
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

    renderThread(messages);

    expect(screen.queryByRole("button", { name: /показать журнал выполнения/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sql optimization advisor/i)).not.toBeInTheDocument();
  });

  it("renders semantic table scoring inside the system log metadata tool card", () => {
    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-metadata",
        role: "assistant",
        text: "Нашёл подходящую таблицу",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        toolCalls: [
          {
            id: "call-metadata",
            tool_name: "metadata_table_scoring",
            arguments: {
              search: "gmv",
              limit: 10,
            },
            result_summary: "Found 3 tables",
            success: true,
            started_at: "2026-04-04T09:00:01Z",
            completed_at: "2026-04-04T09:00:02Z",
            sequence: 1,
          },
        ],
        payload: {
          answer: "Нашёл подходящую таблицу",
          conversation_id: "conv-metadata",
          generated_sql: null,
          optimization_trace: null,
          capability_trace: {
            available_tools: ["catalog__search"],
            used_tools: ["catalog__search"],
            used_providers: ["mcp:catalog"],
            evidence_origin: ["metadata"],
            degraded_capabilities: [],
            policy_decisions: [],
            runtime_context: {
              metadata_table_columns: {},
              semantic_grounding: {
                chosen_table_fqn: "Clickhouse-Sales.default.dm.ft_gmv_daily",
                selection_rationale: "Best governed fact table",
                candidates: [
                  {
                    fqn: "Clickhouse-Sales.default.dm.ft_gmv_daily",
                    service: "Clickhouse-Sales",
                    database: "default",
                    schema: "dm",
                    relevance_score: 98.4,
                    matched_on: ["name_exact", "business_schema"],
                    reason_codes: ["governance_strength", "business_layer_preference"],
                    certification: "Certification.Gold",
                    tier: "Tier.Tier1",
                    owners: ["analytics"],
                    owner_teams: ["analytics-team"],
                    domains: ["sales"],
                    glossary_terms: ["GMV"],
                    related_metrics: ["Gross Merchandise Value"],
                    quality_status: "failed",
                    quality_summary: {
                      status: "failed",
                      total: 2,
                      success: 1,
                      failed: 1,
                      aborted: 0,
                      failed_tests: ["gmv_not_null"],
                    },
                    usage_count: 120,
                    suspicious: false,
                  },
                  {
                    fqn: "Clickhouse-Sales.default.raw.gmv_events",
                    service: "Clickhouse-Sales",
                    database: "default",
                    schema: "raw",
                    relevance_score: 51.2,
                    matched_on: ["name_partial"],
                    reason_codes: ["raw_layer_penalty"],
                    certification: null,
                    tier: null,
                    owners: [],
                    owner_teams: [],
                    domains: [],
                    glossary_terms: [],
                    related_metrics: [],
                    quality_status: null,
                    quality_summary: null,
                    usage_count: 5,
                    suspicious: true,
                  },
                ],
              },
            },
          },
          selected_database_connector: null,
          assistant_message_id: "msg-metadata",
          metadata_sources: ["mcp"],
          context_tables: ["Clickhouse-Sales.default.dm.ft_gmv_daily"],
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

    renderThread(messages);

    fireEvent.click(screen.getByRole("button", { name: /показать журнал выполнения/i }));

    expect(screen.getByText(/metadata table scoring/i)).toBeInTheDocument();
    expect(screen.getByText(/выбрано:/i)).toBeInTheDocument();
    expect(screen.getAllByText("Clickhouse-Sales.default.dm.ft_gmv_daily")).toHaveLength(2);
    expect(screen.getByText(/оценка: 98.4/i)).toBeInTheDocument();
    expect(screen.getByText(/причины: governance_strength, business_layer_preference/i)).toBeInTheDocument();
    expect(screen.getByText("команда: analytics-team")).toBeInTheDocument();
    expect(screen.getByText("термин: GMV")).toBeInTheDocument();
    expect(screen.getByText("метрика: Gross Merchandise Value")).toBeInTheDocument();
    expect(screen.getByText(/качество: failed \(1\/2 успешно\)/i)).toBeInTheDocument();
  });

  it("copies only assistant message text without header and system log", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const messages: ChatMessageViewModel[] = [
      {
        id: "assistant-copy",
        role: "assistant",
        text: "Final answer",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        thinkingBlocks: [
          {
            id: "thinking-1",
            content: "Inspecting schema",
            timestamp: "2026-04-04T09:00:01Z",
          },
        ],
        toolCalls: [
          {
            id: "tool-1",
            tool_name: "catalog__search",
            arguments: { entityType: "table", query: "orders" },
            result_summary: "Found 3 entities",
            success: true,
            started_at: "2026-04-04T09:00:02Z",
            completed_at: "2026-04-04T09:00:03Z",
          },
        ],
        executionStatuses: [
          {
            status: "running",
            message: "Executing query",
            timestamp: "2026-04-04T09:00:04Z",
          },
        ],
      },
    ];

    renderThread(messages);

    fireEvent.click(screen.getByRole("button", { name: /скопировать сообщение/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    expect(writeText).toHaveBeenCalledWith("Final answer");
  });

  it("copies report-like structure for assistant report messages", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    renderThread([
      {
        id: "assistant-report-copy",
        backendMessageId: "msg-report-copy",
        role: "assistant",
        text: "GMV recovered on paid traffic",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        payload: reportPayload,
      },
    ]);

    fireEvent.click(screen.getByRole("button", { name: /скопировать сообщение/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    const copiedText = writeText.mock.calls[0]?.[0] ?? "";
    expect(copiedText).toContain("# Weekly GMV recovery");
    expect(copiedText).toContain("## Краткое резюме");
    expect(copiedText).toContain("- GMV: $148K");
    expect(copiedText).toContain("### График: GMV trend");
    expect(copiedText).toContain("## SQL");
    expect(copiedText).toContain("select day, gmv from dm.gmv_daily");
    expect(copiedText).not.toContain("Analyst note");
    expect(copiedText).not.toContain("llm_finalizer_requested");
    expect(copiedText).not.toContain("## Narrative answer");
  });

  it("copies a user message", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const messages: ChatMessageViewModel[] = [
      {
        id: "user-copy",
        role: "user",
        text: "Почему GMV просел по неделям?",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
      },
    ];

    renderThread(messages);

    fireEvent.click(screen.getByRole("button", { name: /скопировать сообщение/i }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    expect(writeText).toHaveBeenCalledWith("Почему GMV просел по неделям?");
  });

  it("collects expanded feedback before saving a negative reaction", () => {
    const onFeedback = vi.fn();
    renderThread(
      [
        {
          id: "assistant-feedback",
          backendMessageId: "msg-feedback",
          role: "assistant",
          text: "GMV вырос на 10%.",
          timestamp: new Date("2026-04-04T09:00:00Z"),
          deliveryState: "complete",
        },
      ],
      "Ada Lovelace",
      { onFeedback }
    );

    const negativeFeedbackButton = screen.getByRole("button", { name: "Не полезно" });
    fireEvent.mouseEnter(negativeFeedbackButton);
    expect(screen.queryByText("Ответ нужно улучшить")).not.toBeInTheDocument();

    fireEvent.click(negativeFeedbackButton);

    expect(onFeedback).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Что пошло не так?")).toBeInTheDocument();
    expect(screen.getByLabelText("Как нужно было ответить?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить отзыв" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Что пошло не так?"), {
      target: { value: "Неверный период сравнения" },
    });
    fireEvent.change(screen.getByLabelText("Как нужно было ответить?"), {
      target: { value: "Сравнить с тем же периодом прошлого года" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Отправить отзыв" }));

    expect(onFeedback).toHaveBeenCalledWith(
      "msg-feedback",
      "wrong",
      undefined,
      {
        whatWentWrong: "Неверный период сравнения",
        expectedAnswer: "Сравнить с тем же периодом прошлого года",
      }
    );
    expect(screen.queryByLabelText("Что пошло не так?")).not.toBeInTheDocument();
  });

  it("saves a useful reaction without opening the expanded form", () => {
    const onFeedback = vi.fn();
    renderThread(
      [
        {
          id: "assistant-useful-feedback",
          backendMessageId: "msg-useful-feedback",
          role: "assistant",
          text: "GMV вырос на 10%.",
          timestamp: new Date("2026-04-04T09:00:00Z"),
          deliveryState: "complete",
        },
      ],
      "Ada Lovelace",
      { onFeedback }
    );

    fireEvent.click(screen.getByRole("button", { name: "Полезно" }));

    expect(onFeedback).toHaveBeenCalledWith("msg-useful-feedback", "useful", undefined, undefined);
    expect(screen.queryByLabelText("Что пошло не так?")).not.toBeInTheDocument();
  });

  it("keeps an unsaved draft but clears it after persisted feedback becomes useful", () => {
    const message: ChatMessageViewModel = {
      id: "assistant-draft",
      backendMessageId: "msg-draft",
      role: "assistant",
      text: "GMV вырос на 10%.",
      timestamp: new Date("2026-04-04T09:00:00Z"),
      deliveryState: "complete",
      feedback: null,
    };
    const view = renderThread([message]);
    fireEvent.click(screen.getByRole("button", { name: "Не полезно" }));
    fireEvent.change(screen.getByLabelText("Что пошло не так?"), { target: { value: "Черновик" } });

    view.rerender(
      <ToastProvider>
        <ChatThread chatWidth={1100} conversationId="conversation-test" messages={[message]} loading={false} feedbackPendingByMessageId={{}} onFeedback={() => undefined} onOpenScenarioCatalog={() => undefined} />
      </ToastProvider>
    );
    expect(screen.getByLabelText("Что пошло не так?")).toHaveValue("Черновик");

    view.rerender(
      <ToastProvider>
        <ChatThread chatWidth={1100} conversationId="conversation-test" messages={[{ ...message, feedback: "useful" }]} loading={false} feedbackPendingByMessageId={{}} onFeedback={() => undefined} onOpenScenarioCatalog={() => undefined} />
      </ToastProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Не полезно" }));
    expect(screen.getByLabelText("Что пошло не так?")).toHaveValue("");
  });

  it("forwards repair action from a negative feedback card", () => {
    const onRepairTurn = vi.fn();
    renderThread(
      [{
        id: "assistant-repair",
        backendMessageId: "msg-repair",
        role: "assistant",
        text: "GMV вырос на 10%.",
        timestamp: new Date("2026-04-04T09:00:00Z"),
        deliveryState: "complete",
        feedback: "wrong",
        feedbackWhatWentWrong: "Неверный период",
        feedbackExpectedAnswer: "Нужен аналогичный период",
      }],
      "Ada Lovelace",
      { onRepairTurn }
    );

    fireEvent.click(screen.getByRole("button", { name: "Исправить ответ" }));
    expect(onRepairTurn).toHaveBeenCalledWith("msg-repair");
  });

  it("blocks repair while negative feedback is being saved", () => {
    const onRepairTurn = vi.fn();
    renderThread(
      [{
        id: "assistant-repair-pending",
        backendMessageId: "msg-repair-pending",
        role: "assistant",
        text: "Ответ, который нужно исправить",
        timestamp: new Date("2026-01-01T00:00:00Z"),
        deliveryState: "complete",
        feedback: "wrong",
        feedbackWhatWentWrong: "Неверный период",
        feedbackExpectedAnswer: "Нужен аналогичный период",
      }],
      "Ada Lovelace",
      {
        feedbackPendingByMessageId: { "msg-repair-pending": true },
        onRepairTurn,
      }
    );

    const repairButton = screen.getByRole("button", { name: "Исправить ответ" });
    expect(repairButton).toBeDisabled();
    fireEvent.click(repairButton);
    expect(onRepairTurn).not.toHaveBeenCalled();
  });
});
