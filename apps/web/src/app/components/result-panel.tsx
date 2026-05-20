"use client";

import { motion } from "framer-motion";
import { Badge, Tabs, TabsList, TabsTrigger, TabsContent, ScrollArea } from "@rhetorica/ui";
import { Database, Table as TableIcon, BarChart3 } from "lucide-react";
import { VisualizationChart, type QueryPreview, type VisualizationSpec } from "@/features/charts";
import { SQLEditor } from "./sql-editor";
import { ResultTable } from "./result-table";
import { StatusBadge } from "./status-badge";

type ExecuteSqlResponse = {
  execution_id: string;
  status: string;
  engine: string;
  normalized_sql: string | null;
  row_count: number | null;
  duration_ms: number | null;
  error_message: string | null;
  preview: QueryPreview;
};

type ChatAskResponse = {
  answer: string;
  generated_sql: string | null;
  selected_database_connector: string | null;
  metadata_sources: string[];
  context_tables: string[];
  claims: Array<{ claim_id: string; text: string; status: "verified" | "unverified" }>;
  evidence: Array<{
    evidence_id: string;
    type: "execution_result" | "metadata_entity" | "rag_insight" | "confluence_page";
    reference: string;
    title?: string | null;
    snippet?: string | null;
    status: "verified" | "unverified";
  }>;
  claim_evidence_links: Array<{ claim_id: string; evidence_ids: string[] }>;
  uncertainty_flags: string[];
  execution: ExecuteSqlResponse | null;
  visualization: VisualizationSpec | null;
  warnings: string[];
};

interface ResultPanelProps {
  payload?: ChatAskResponse;
  recentExecutions?: Array<{
    execution_id: string;
    status: string;
    submitted_sql: string;
    updated_at: string;
    duration_ms: number | null;
    row_count: number | null;
  }>;
}

function shortenSql(sql: string): string {
  const compact = sql.replace(/\s+/g, " ").trim();
  return compact.length > 84 ? `${compact.slice(0, 84)}...` : compact;
}

function formatExecutionTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  const seconds = String(date.getUTCSeconds()).padStart(2, "0");

  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds} UTC`;
}

export function ResultPanel({ payload, recentExecutions = [] }: ResultPanelProps) {
  const execution = payload?.execution;
  const hasData = execution?.preview?.rows && execution.preview.rows.length > 0;
  const hasVisualization = Boolean(
    payload?.visualization && execution?.preview?.rows && execution.preview.rows.length > 0
  );
  const visualization = payload?.visualization;
  const defaultTab = hasVisualization ? "table" : payload?.generated_sql ? "sql" : "table";

  return (
    <motion.aside
      initial={{ x: 20, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.4, delay: 0.1 }}
      className="panel result-panel flex flex-col"
    >
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-[#5da8ff]" />
          <h2 className="panel-title">Execution Result</h2>
        </div>
        <Badge variant="primary" className="gap-1.5">
          <Database className="h-3 w-3" />
          {payload?.selected_database_connector ?? "Not selected"}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-2 px-4 pt-3">
        <Badge variant="info">
          Sources: {payload?.metadata_sources.join(", ") || "-"}
        </Badge>
        {execution ? <StatusBadge status={execution.status} /> : null}
        <Badge variant="default">
          <TableIcon className="h-3 w-3 mr-1" />
          Rows: {execution?.row_count ?? 0}
        </Badge>
        {execution?.duration_ms ? (
          <Badge variant="default">{execution.duration_ms}ms</Badge>
        ) : null}
      </div>

      <ScrollArea className="flex-1 p-4">
        <div className="space-y-4">
          {visualization ? (
            <div className="metric-grid !mt-0 !mx-0 !mb-0">
              <div className="metric-card">
                <div className="metric-label">Rows</div>
                <div className="metric-value">{execution?.row_count ?? 0}</div>
                <div className="metric-change text-[#9bb0d3]">guarded execution preview</div>
              </div>
              <div className="metric-card">
                <div className="metric-label">Confidence</div>
                <div className="metric-value">
                  {Math.round(visualization.confidence_score * 100)}%
                </div>
                <div className="metric-change text-[#9bb0d3]">{visualization.planner_mode}</div>
              </div>
              <div className="metric-card">
                <div className="metric-label">Preview</div>
                <div className="metric-value">
                  {visualization.chart_safe_on_preview ? "safe" : "limited"}
                </div>
                <div className="metric-change text-[#9bb0d3]">
                  {visualization.goal}
                </div>
              </div>
            </div>
          ) : null}

          {execution?.error_message ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-lg border border-[#ff6d7c]/30 bg-[#ff6d7c]/10"
            >
              <p className="text-sm text-[#ff6d7c] font-medium">Execution Error</p>
              <p className="text-xs text-[#ff6d7c]/80 mt-1">
                {execution.error_message}
              </p>
            </motion.div>
          ) : null}

          {payload?.warnings?.length ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-lg border border-[#f2b14b]/30 bg-[#f2b14b]/10"
            >
              <p className="text-sm text-[#f2b14b] font-medium">Warnings</p>
              <ul className="text-xs text-[#f2b14b]/80 mt-2 space-y-1">
                {payload.warnings.map((warning, index) => (
                  <li key={index}>• {warning}</li>
                ))}
              </ul>
            </motion.div>
          ) : null}

          {hasVisualization ? (
            <VisualizationChart
              spec={payload?.visualization}
              preview={execution?.preview}
            />
          ) : null}

          <Tabs defaultValue={defaultTab} className="w-full">
            <TabsList className="w-full flex-wrap justify-start gap-2 bg-transparent border-0 p-0">
              <TabsTrigger
                value="table"
                className="data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
              >
                <TableIcon className="h-4 w-4 mr-1.5" />
                Table
              </TabsTrigger>
              <TabsTrigger
                value="sql"
                className="data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                disabled={!payload?.generated_sql}
              >
                SQL
              </TabsTrigger>
              <TabsTrigger
                value="evidence"
                className="data-[state=active]:bg-[#5da8ff] data-[state=active]:text-white"
                disabled={!payload?.claims?.length}
              >
                Evidence
              </TabsTrigger>
            </TabsList>

            <TabsContent value="table" className="mt-4">
              {hasData ? (
                <ResultTable
                  columns={execution!.preview.columns}
                  rows={execution!.preview.rows}
                />
              ) : (
                <ResultTable columns={[]} rows={[]} />
              )}
            </TabsContent>

            <TabsContent value="sql" className="mt-4">
              {payload?.generated_sql ? (
                <SQLEditor
                  sql={payload.generated_sql}
                  engine={execution?.engine ?? "SQL"}
                />
              ) : null}
            </TabsContent>

            <TabsContent value="evidence" className="mt-4">
              {payload?.claims?.length ? (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-4 rounded-lg border border-[#5da8ff]/25 bg-[#5da8ff]/8"
                >
                  <p className="text-sm text-[#9dc7ff] font-medium">Evidence</p>
                  <ul className="mt-2 space-y-2 text-xs text-[#d7e7ff]">
                    {payload.claims.slice(0, 6).map((claim) => {
                      const link = payload.claim_evidence_links.find(
                        (item) => item.claim_id === claim.claim_id
                      );
                      const linkedEvidence = (link?.evidence_ids ?? [])
                        .map((id) => payload.evidence.find((item) => item.evidence_id === id))
                        .filter((item): item is NonNullable<typeof item> => item != null);
                      return (
                        <li key={claim.claim_id} className="space-y-1">
                          <div>
                            <span className="font-medium">
                              [{claim.status === "verified" ? "verified" : "unverified"}]
                            </span>{" "}
                            {claim.text}
                          </div>
                          {linkedEvidence.length ? (
                            <div className="text-[#9ec1f0]">
                              sources:{" "}
                              {linkedEvidence
                                .map((item) => `${item.type}:${item.reference}`)
                                .join(", ")}
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                  {payload.uncertainty_flags?.length ? (
                    <div className="mt-3 text-xs text-[#f2b14b]">
                      uncertainty: {payload.uncertainty_flags.join(", ")}
                    </div>
                  ) : null}
                </motion.div>
              ) : null}
            </TabsContent>
          </Tabs>

          {!hasVisualization && payload?.claims?.length ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-lg border border-[#5da8ff]/25 bg-[#5da8ff]/8"
            >
              <p className="text-sm text-[#9dc7ff] font-medium">Evidence</p>
              <ul className="mt-2 space-y-2 text-xs text-[#d7e7ff]">
                {payload.claims.slice(0, 6).map((claim) => {
                  const link = payload.claim_evidence_links.find(
                    (item) => item.claim_id === claim.claim_id
                  );
                  const linkedEvidence = (link?.evidence_ids ?? [])
                    .map((id) => payload.evidence.find((item) => item.evidence_id === id))
                    .filter((item): item is NonNullable<typeof item> => item != null);
                  return (
                    <li key={claim.claim_id} className="space-y-1">
                      <div>
                        <span className="font-medium">
                          [{claim.status === "verified" ? "verified" : "unverified"}]
                        </span>{" "}
                        {claim.text}
                      </div>
                      {linkedEvidence.length ? (
                        <div className="text-[#9ec1f0]">
                          sources:{" "}
                          {linkedEvidence
                            .map((item) => `${item.type}:${item.reference}`)
                            .join(", ")}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {payload.uncertainty_flags?.length ? (
                <div className="mt-3 text-xs text-[#f2b14b]">
                  uncertainty: {payload.uncertainty_flags.join(", ")}
                </div>
              ) : null}
            </motion.div>
          ) : null}

          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-lg border border-[#24324d] bg-[#0d1424]/80 p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-[#d7e7ff]">Recent Executions</p>
              <Badge variant="default">{recentExecutions.length}</Badge>
            </div>

            <div className="mt-3 space-y-2">
              {recentExecutions.length ? (
                recentExecutions.map((item) => (
                  <div
                    key={item.execution_id}
                    className="rounded-lg border border-[#1f2a3f] bg-[#09111d] px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <StatusBadge status={item.status} />
                      <span className="text-[11px] text-[#7484a3]">
                        {formatExecutionTimestamp(item.updated_at)}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-[#edf3ff]">
                      {shortenSql(item.submitted_sql)}
                    </p>
                    <div className="mt-2 flex items-center gap-3 text-[11px] text-[#9bb0d3]">
                      <span>id: {item.execution_id.slice(0, 8)}</span>
                      <span>rows: {item.row_count ?? "-"}</span>
                      <span>time: {item.duration_ms ? `${item.duration_ms}ms` : "-"}</span>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-xs text-[#7484a3]">
                  No executions yet. Submit one and the queue lifecycle will show up here.
                </p>
              )}
            </div>
          </motion.div>
        </div>
      </ScrollArea>
    </motion.aside>
  );
}
