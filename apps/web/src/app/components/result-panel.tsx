"use client";

import { motion } from "framer-motion";
import { Badge, Tabs, TabsList, TabsTrigger, TabsContent, ScrollArea } from "@rhetorica/ui";
import { Database, Table as TableIcon, BarChart3 } from "lucide-react";
import { VisualizationChart, type QueryPreview, type VisualizationSpec } from "@/features/charts";
import { SQLEditor } from "./sql-editor";
import { ResultTable } from "./result-table";
import { StatusBadge } from "./status-badge";
import { DISPLAY_TIME_ZONE_LABEL, formatInMoscow } from "@/lib/dateTime";

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
    type:
      | "execution_result"
      | "metadata_entity"
      | "gitlab_file"
      | "airflow_runtime"
      | "rag_insight"
      | "internal_documentation";
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
  const formatted = formatInMoscow(
    timestamp,
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    },
    "sv-SE"
  );
  return formatted ? `${formatted} ${DISPLAY_TIME_ZONE_LABEL}` : timestamp;
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
          <BarChart3 className="h-4 w-4 text-accent" />
          <h2 className="panel-title">Результат выполнения</h2>
        </div>
        <Badge variant="primary" className="gap-1.5">
          <Database className="h-3 w-3" />
          {payload?.selected_database_connector ?? "Не выбрано"}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-2 px-4 pt-3">
        <Badge variant="info">
          Источники: {payload?.metadata_sources.join(", ") || "-"}
        </Badge>
        {execution ? <StatusBadge status={execution.status} /> : null}
        <Badge variant="default">
          <TableIcon className="h-3 w-3 mr-1" />
          Строк: {execution?.row_count ?? 0}
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
                <div className="metric-label">Строк</div>
                <div className="metric-value">{execution?.row_count ?? 0}</div>
                <div className="metric-change text-secondary">превью защищённого выполнения</div>
              </div>
              <div className="metric-card">
                <div className="metric-label">Уверенность</div>
                <div className="metric-value">
                  {Math.round(visualization.confidence_score * 100)}%
                </div>
                <div className="metric-change text-secondary">{visualization.planner_mode}</div>
              </div>
              <div className="metric-card">
                <div className="metric-label">Превью</div>
                <div className="metric-value">
                  {visualization.chart_safe_on_preview ? "безопасно" : "ограничено"}
                </div>
                <div className="metric-change text-secondary">
                  {visualization.goal}
                </div>
              </div>
            </div>
          ) : null}

          {execution?.error_message ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-lg border border-danger/30 bg-danger/10"
            >
              <p className="text-sm text-danger font-medium">Ошибка выполнения</p>
              <p className="text-xs text-danger/80 mt-1">
                {execution.error_message}
              </p>
            </motion.div>
          ) : null}

          {payload?.warnings?.length ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-lg border border-warning/30 bg-warning/10"
            >
              <p className="text-sm text-warning font-medium">Предупреждения</p>
              <ul className="text-xs text-warning/80 mt-2 space-y-1">
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
                className="data-[state=active]:bg-accent data-[state=active]:text-on-accent"
              >
                <TableIcon className="h-4 w-4 mr-1.5" />
                Таблица
              </TabsTrigger>
              <TabsTrigger
                value="sql"
                className="data-[state=active]:bg-accent data-[state=active]:text-on-accent"
                disabled={!payload?.generated_sql}
              >
                SQL
              </TabsTrigger>
              <TabsTrigger
                value="evidence"
                className="data-[state=active]:bg-accent data-[state=active]:text-on-accent"
                disabled={!payload?.claims?.length}
              >
                Доказательства
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
                  className="p-4 rounded-lg border border-accent/25 bg-accent/[0.08]"
                >
                  <p className="text-sm text-accent font-medium">Доказательства</p>
                  <ul className="mt-2 space-y-2 text-xs text-primary">
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
                              [{claim.status === "verified" ? "Подтверждено" : "Не проверено"}]
                            </span>{" "}
                            {claim.text}
                          </div>
                          {linkedEvidence.length ? (
                            <div className="text-accent">
                              источники:{" "}
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
                    <div className="mt-3 text-xs text-warning">
                      неопределённость: {payload.uncertainty_flags.join(", ")}
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
              className="p-4 rounded-lg border border-accent/25 bg-accent/[0.08]"
            >
              <p className="text-sm text-accent font-medium">Доказательства</p>
              <ul className="mt-2 space-y-2 text-xs text-primary">
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
                          [{claim.status === "verified" ? "Подтверждено" : "Не проверено"}]
                        </span>{" "}
                        {claim.text}
                      </div>
                      {linkedEvidence.length ? (
                        <div className="text-accent">
                          источники:{" "}
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
                <div className="mt-3 text-xs text-warning">
                  неопределённость: {payload.uncertainty_flags.join(", ")}
                </div>
              ) : null}
            </motion.div>
          ) : null}

          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-lg border border-border-strong bg-surface/80 p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-primary">Последние выполнения</p>
              <Badge variant="default">{recentExecutions.length}</Badge>
            </div>

            <div className="mt-3 space-y-2">
              {recentExecutions.length ? (
                recentExecutions.map((item) => (
                  <div
                    key={item.execution_id}
                    className="rounded-lg border border-border bg-surface px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <StatusBadge status={item.status} />
                      <span className="text-[11px] text-muted">
                        {formatExecutionTimestamp(item.updated_at)}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-primary">
                      {shortenSql(item.submitted_sql)}
                    </p>
                    <div className="mt-2 flex items-center gap-3 text-[11px] text-secondary">
                      <span>id: {item.execution_id.slice(0, 8)}</span>
                      <span>строк: {item.row_count ?? "-"}</span>
                      <span>время: {item.duration_ms ? `${item.duration_ms}ms` : "-"}</span>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-xs text-muted">
                  Выполнений пока нет. Отправьте запрос — жизненный цикл очереди отобразится здесь.
                </p>
              )}
            </div>
          </motion.div>
        </div>
      </ScrollArea>
    </motion.aside>
  );
}
