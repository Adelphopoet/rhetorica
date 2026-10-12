import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatAskResponse } from "@/features/chat/chatViewModel";
import { ToastProvider } from "@rhetorica/ui";

vi.mock("@/features/charts", () => ({
  VisualizationChart: ({ preview }: { preview?: { rows?: unknown[] } | null }) => (
    <div>Mock chart rows: {preview?.rows?.length ?? 0}</div>
  ),
}));

vi.mock("./result-table", () => ({
  ResultTable: ({ rows }: { rows: unknown[] }) => <div>Rows loaded: {rows.length}</div>,
}));

vi.mock("./sql-editor", () => ({
  SQLEditor: ({ sql }: { sql: string }) => <div>SQL editor: {sql}</div>,
}));

import { calculateNextDesktopPanelWidth, ResultSurface } from "./result-surface";
import { buildResultSurfaceViewModel } from "@/features/chat/chatViewModel";

const payload: ChatAskResponse = {
  answer: "Weekly GMV is trending up",
  conversation_id: "conv-1",
  generated_sql: "select week, gmv from metrics.weekly_gmv",
  optimization_trace: null,
  selected_database_connector: "clickhouse-main",
  assistant_message_id: "msg-1",
  metadata_sources: ["dbt", "mcp"],
  context_tables: ["metrics.weekly_gmv"],
  claims: [
    {
      claim_id: "claim-1",
      text: "GMV increased week over week",
      status: "verified",
    },
  ],
  evidence: [
    {
      evidence_id: "evidence-1",
      type: "execution_result",
      reference: "exec-1",
      status: "verified",
    },
  ],
  claim_evidence_links: [{ claim_id: "claim-1", evidence_ids: ["evidence-1"] }],
  uncertainty_flags: [],
  execution: {
    execution_id: "exec-1",
    status: "succeeded",
    engine: "clickhouse",
    normalized_sql: "select week, gmv from metrics.weekly_gmv",
    row_count: 2,
    duration_ms: 140,
    error_message: null,
    preview: {
      columns: ["week", "gmv"],
      rows: [
        ["2026-W13", 1200],
        ["2026-W14", 1500],
      ],
    },
  },
  analysis_document: null,
  visualization: null,
  warnings: [],
};

const optimizedPayload: ChatAskResponse = {
  ...payload,
  optimization_trace: {
    optimizer_used: true,
    optimizer_provider: "clickhouse_rule_advisor",
    input_sql: "select week, gmv from metrics.weekly_gmv",
    optimized_sql_draft: "select week, gmv from metrics.weekly_gmv where week >= '2026-W13'",
    input_sql_hash: "hash-1",
    optimized_sql_hash: "hash-2",
    accepted_or_rejected: "accepted",
    applied: true,
    risk_level: "low",
    confidence: 0.91,
    engine_scope: "clickhouse",
    rules_triggered: ["query-join-filter-before"],
    findings: [
      {
        rule_id: "query-join-filter-before",
        title: "Filter can be pushed below INNER JOIN",
        summary: "Single-table predicates can move closer to scans",
        severity: "info",
        applies_to: ["orders"],
        citations: [],
      },
    ],
    recommended_actions: [
      {
        action_id: "pushdown_predicates",
        kind: "rewrite",
        title: "Push down single-table predicates before INNER JOIN",
        summary: "Safe single-table predicates were moved closer to source scans",
        sql: "select week, gmv from metrics.weekly_gmv where week >= '2026-W13'",
        applied: true,
        citations: [],
      },
    ],
    citations: [
      {
        kind: "rule",
        label: "query-join-filter-before",
        reference: "query-join-filter-before",
      },
    ],
    warnings: [],
    skills_used: ["clickhouse-best-practices"],
    evidence_used: ["ddl", "system.tables", "system.columns", "explain:indexes=1"],
  },
};

const reportPayload: ChatAskResponse = {
  ...payload,
  analysis_document: {
    document_version: "v1",
    title: "Financial report",
    executive_summary: "GMV grew week over week",
    warnings: [],
    provenance: {
      source_type: "chat_answer",
      execution_id: "exec-1",
      planner_mode: "rule_fallback",
    },
    blocks: [
      {
        kind: "finding",
        title: "Key takeaway",
        body: "GMV grew week over week",
        emphasis: "important",
      },
    ],
  },
};

describe("ResultSurface", () => {
  function renderSurface(overridePayload: ChatAskResponse = payload) {
    return render(
      <ToastProvider>
        <ResultSurface
          payload={overridePayload}
          recentExecutions={[]}
          isOpen
          onClose={() => undefined}
        />
      </ToastProvider>
    );
  }

  it("does not render the rail before the first result payload exists", () => {
    render(
      <ToastProvider>
        <ResultSurface
          payload={undefined}
          recentExecutions={[]}
          isOpen={false}
          onClose={() => undefined}
        />
      </ToastProvider>
    );

    expect(screen.queryByText(/панель проверки аналитика/i)).not.toBeInTheDocument();
  });

  it("renders result, SQL, and evidence tabs", async () => {
    renderSurface();

    expect(await screen.findByText(/rows loaded: 2/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /sql/i }));
    expect(await screen.findByText(/sql editor:/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /доказательств/i }));
    expect(screen.getByText(/gmv increased week over week/i)).toBeInTheDocument();
  });

  it("hides report exports for plain answers", () => {
    renderSurface();

    expect(screen.queryByRole("button", { name: /экспорт/i })).not.toBeInTheDocument();
  });

  it("keeps report export formats behind one menu button", async () => {
    renderSurface(reportPayload);

    expect(screen.getByRole("button", { name: /экспорт/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /html/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /экспорт/i }));

    expect(await screen.findByRole("menuitem", { name: /html/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /pdf/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /ipynb/i })).toBeInTheDocument();
  });

  it("shows export for standalone visualization results", async () => {
    renderSurface({
      ...payload,
      visualization: {
        version: "v2",
        family: "temporal",
        kind: "line",
        goal: "trend",
        title: "Orders by day",
        summary: "Orders change by date",
        description: null,
        planner_mode: "rule_fallback",
        confidence_score: 0.8,
        chart_safe_on_preview: true,
        x_axis_label: "week",
        y_axis_label: "gmv",
        value_format: "integer",
        metric_unit: null,
        data_source: {
          type: "execution_preview",
          execution_id: "exec-1",
          row_count: 2,
          is_truncated: false,
        },
        encoding: {
          x: "week",
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
          limit: 20,
          aggregation: "none",
          top_n: null,
          bin_size: null,
          window: null,
          pivot: null,
          normalize: "none",
          cumulative: false,
        },
        annotations: [],
        warnings: [],
        rationale: {
          why_kind: "trend",
          why_fields: ["week", "gmv"],
        },
      },
    });

    expect(await screen.findByRole("button", { name: /экспорт/i })).toBeInTheDocument();
  });

  it("renders report blocks from block-local previews", async () => {
    renderSurface({
      ...payload,
      execution: null,
      analysis_document: {
        document_version: "v1",
        title: "Financial report",
        executive_summary: "Two slices were prepared",
        warnings: [],
        provenance: {
          source_type: "chat_answer",
          execution_id: "exec-2",
          planner_mode: "rule_fallback",
        },
        blocks: [
          {
            kind: "chart",
            title: "By delivery",
            description: null,
            insight: "Express wins",
            source_execution_id: "exec-1",
            preview: {
              columns: ["delivery_type", "total_sales"],
              rows: [["DELIVERY_EXPRESS", 10], ["DELIVERY_SELF", 4]],
            },
            visualization: {
              version: "v2",
              family: "categorical",
              kind: "bar",
              goal: "comparison",
              title: "By delivery",
              summary: "Express wins",
              description: null,
              planner_mode: "rule_fallback",
              confidence_score: 0.8,
              chart_safe_on_preview: true,
              x_axis_label: "delivery_type",
              y_axis_label: "total_sales",
              value_format: "currency",
              metric_unit: "RUB",
              data_source: {
                type: "execution_preview",
                execution_id: "exec-1",
                row_count: 2,
                is_truncated: false,
              },
              encoding: {
                x: "delivery_type",
                y: ["total_sales"],
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
                sort: "desc",
                limit: 20,
                aggregation: "none",
                top_n: null,
                bin_size: null,
                window: null,
                pivot: null,
                normalize: "none",
                cumulative: false,
              },
              annotations: [],
              warnings: [],
              rationale: {
                why_kind: "comparison",
                why_fields: ["delivery_type", "total_sales"],
              },
            },
          },
          {
            kind: "table",
            title: "City rows",
            description: "Rows for city slice",
            source: "execution_preview",
            source_execution_id: "exec-2",
            preview: {
              columns: ["city_id", "total_sales"],
              rows: [[2878, 20], [3338, 15], [2439, 11]],
            },
            columns: ["city_id", "total_sales"],
            preferred_hidden: true,
            action_label: "Show rows",
          },
        ],
      },
      visualization: null,
    });

    expect(await screen.findByText(/mock chart rows: 2/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show rows/i }));
    expect(await screen.findByText(/rows loaded: 3/i)).toBeInTheDocument();
  });

  it("does not duplicate executive summary when the document also includes key takeaway", async () => {
    const summary = "Запрос выполнен. Данные есть только за первые 4 дня апреля";

    renderSurface({
      ...payload,
      analysis_document: {
        document_version: "v1",
        title: "Financial report",
        executive_summary: summary,
        warnings: [],
        provenance: {
          source_type: "chat_answer",
          execution_id: "exec-1",
          planner_mode: "rule_fallback",
        },
        blocks: [
          {
            kind: "finding",
            title: "Key takeaway",
            body: summary,
            emphasis: "important",
          },
        ],
      },
    });

    expect(await screen.findByText(/краткое резюме/i)).toBeInTheDocument();
    expect(screen.getAllByText(summary)).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: /key takeaway/i })).not.toBeInTheDocument();
  });

  it("renders optimizer trace with original and executed SQL", async () => {
    renderSurface(optimizedPayload);

    fireEvent.click(screen.getByRole("button", { name: /оптимизатор/i }));

    expect(await screen.findByText(/push down single-table predicates/i)).toBeInTheDocument();
    expect(screen.getByText(/исходный sql/i)).toBeInTheDocument();
    expect(screen.getByText(/выполненный sql/i)).toBeInTheDocument();
    expect(screen.getByText(/использованные доказательства/i)).toBeInTheDocument();
  });

  it("keeps recent executions hidden until expanded", () => {
    render(
      <ToastProvider>
        <ResultSurface
          payload={payload}
          recentExecutions={[
            {
              execution_id: "exec-2",
              status: "succeeded",
              submitted_sql: "select * from metrics.weekly_gmv limit 10",
              updated_at: "2026-04-04T09:00:00Z",
              duration_ms: 88,
              row_count: 10,
            },
          ]}
          isOpen
          onClose={() => undefined}
        />
      </ToastProvider>
    );

    expect(screen.queryByText(/select \* from metrics/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /последние выполнения/i }));

    expect(screen.getByText(/select \* from metrics/i)).toBeInTheDocument();
  });

  it("deduplicates duplicate warnings before rendering them", () => {
    const payloadWithDuplicateWarnings: ChatAskResponse = {
      ...payload,
      warnings: [
        "sql_preflight_context_repaired",
        "sql_preflight_context_repaired",
        "sql_execution_blocked_unavailable_context_connector",
        "sql_execution_blocked_unavailable_context_connector",
      ],
    };

    const viewModel = buildResultSurfaceViewModel(payloadWithDuplicateWarnings, []);
    expect(viewModel.warnings).toEqual([
      "sql_preflight_context_repaired",
      "sql_execution_blocked_unavailable_context_connector",
    ]);

    render(
      <ToastProvider>
        <ResultSurface
          payload={payloadWithDuplicateWarnings}
          recentExecutions={[]}
          isOpen
          onClose={() => undefined}
        />
      </ToastProvider>
    );

    expect(screen.getAllByText("sql_preflight_context_repaired")).toHaveLength(1);
    expect(
      screen.getAllByText("sql_execution_blocked_unavailable_context_connector")
    ).toHaveLength(1);
  });

  it("renders duplicate claim ids without colliding React keys", async () => {
    const payloadWithDuplicateClaims: ChatAskResponse = {
      ...payload,
      claims: [
        {
          claim_id: ": ",
          text: "First duplicate claim",
          status: "verified",
        },
        {
          claim_id: ": ",
          text: "Second duplicate claim",
          status: "unverified",
        },
      ],
      claim_evidence_links: [],
    };
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    try {
      renderSurface(payloadWithDuplicateClaims);
      fireEvent.click(screen.getByRole("button", { name: /доказательств/i }));

      expect(await screen.findByText(/first duplicate claim/i)).toBeInTheDocument();
      expect(screen.getByText(/second duplicate claim/i)).toBeInTheDocument();
      expect(
        consoleErrorSpy.mock.calls.some((call) =>
          call.some((value) =>
            String(value).includes("Encountered two children with the same key")
          )
        )
      ).toBe(false);
    } finally {
      consoleErrorSpy.mockRestore();
    }
  });

  it("calculates desktop drag width from the current layout edge", () => {
    expect(calculateNextDesktopPanelWidth(900, 1600, 1600)).toBe(700);
  });

  it("uses workspace bounds instead of raw viewport width on wide layouts", () => {
    expect(calculateNextDesktopPanelWidth(1010, 1520, 1910)).toBe(900);
    expect(calculateNextDesktopPanelWidth(1010, 2400, 2400)).toBe(1390);
  });

  it("calls onClose when the panel hide button is pressed", () => {
    const onClose = vi.fn();

    render(
      <ToastProvider>
        <ResultSurface
          payload={payload}
          recentExecutions={[]}
          isOpen
          onClose={onClose}
        />
      </ToastProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: /скрыть/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("starts html export download from persisted snapshot", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Blob(["<html></html>"]), {
        status: 200,
        headers: {
          "Content-Disposition": 'attachment; filename="analysis-export.html"',
          "Content-Type": "text/html; charset=utf-8",
        },
      })
    );
    const createObjectURL = vi.fn(() => "blob:download");
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    const createElement = vi.spyOn(document, "createElement").mockImplementation((tagName: string) => {
      if (tagName === "a") {
        return {
          click,
          set href(_value: string) {},
          set download(_value: string) {},
        } as unknown as HTMLAnchorElement;
      }
      return originalCreateElement(tagName);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      createObjectURL,
      revokeObjectURL,
    });

    renderSurface(reportPayload);

    fireEvent.click(screen.getByRole("button", { name: /экспорт/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /html/i }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/conversations/conv-1/messages/msg-1/exports/html",
      { cache: "no-store" }
    );

    await screen.findByText(/экспорт готов/i);
    expect(click).toHaveBeenCalledTimes(1);

    createElement.mockRestore();
    vi.unstubAllGlobals();
  });
});
