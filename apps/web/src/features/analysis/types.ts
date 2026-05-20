import type { QueryPreview, VisualizationSpec } from "@/features/charts";

export interface AnalysisDocumentProvenance {
  source_type: "chat_answer";
  execution_id?: string | null;
  planner_mode: "rule_fallback" | "llm_validated" | "mixed";
}

export interface AnalysisMetricItem {
  label: string;
  value: string;
  context?: string | null;
  delta?: string | null;
  tone: "neutral" | "positive" | "warning" | "negative";
}

export interface AnalysisSectionHeaderBlock {
  kind: "section_header";
  title: string;
  description?: string | null;
}

export interface AnalysisNarrativeNoteBlock {
  kind: "narrative_note";
  title?: string | null;
  markdown: string;
}

export interface AnalysisFindingBlock {
  kind: "finding";
  title: string;
  body: string;
  emphasis: "info" | "important" | "critical";
}

export interface AnalysisCaveatBlock {
  kind: "caveat";
  title: string;
  body: string;
}

export interface AnalysisMetricGridBlock {
  kind: "metric_grid";
  title?: string | null;
  description?: string | null;
  items: AnalysisMetricItem[];
}

export interface AnalysisChartBlock {
  kind: "chart";
  title?: string | null;
  description?: string | null;
  insight?: string | null;
  source_execution_id?: string | null;
  preview?: QueryPreview | null;
  visualization: VisualizationSpec;
}

export interface AnalysisTableBlock {
  kind: "table";
  title?: string | null;
  description?: string | null;
  source: "execution_preview";
  source_execution_id?: string | null;
  preview?: QueryPreview | null;
  columns: string[];
  preferred_hidden: boolean;
  action_label?: string | null;
}

export interface AnalysisDividerBlock {
  kind: "divider";
}

export type AnalysisBlock =
  | AnalysisSectionHeaderBlock
  | AnalysisNarrativeNoteBlock
  | AnalysisFindingBlock
  | AnalysisCaveatBlock
  | AnalysisMetricGridBlock
  | AnalysisChartBlock
  | AnalysisTableBlock
  | AnalysisDividerBlock;

export interface AnalysisDocument {
  document_version: "v1";
  title?: string | null;
  executive_summary?: string | null;
  blocks: AnalysisBlock[];
  warnings: string[];
  provenance: AnalysisDocumentProvenance;
}
