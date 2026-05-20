"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Badge, Button } from "@rhetorica/ui";
import { Database, ShieldCheck, Sparkles } from "lucide-react";

type ChatEmptyStateProps = {
  scenarioCount: number;
  onOpenScenarioCatalog: () => void;
};

export function ChatEmptyState({
  scenarioCount,
  onOpenScenarioCatalog,
}: ChatEmptyStateProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.section
      initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 18 }}
      animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="flex h-full min-h-[260px] items-center justify-center"
    >
      <div className="w-full max-w-2xl rounded-[28px] border border-[#1b2940] bg-[radial-gradient(circle_at_top_left,rgba(93,168,255,0.12),transparent_28%),linear-gradient(180deg,rgba(10,17,30,0.98),rgba(7,12,22,0.98))] p-6 shadow-[0_24px_70px_rgba(0,0,0,0.3)] sm:p-7">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-[16px] border border-[#335784] bg-[#0d1a2d] text-[#9fd0ff]">
          <Sparkles className="h-5 w-5" />
        </div>
        <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.22em] text-[#8ebcff]">
          New analysis
        </p>
        <h3 className="mt-2 text-[28px] font-semibold tracking-[-0.04em] text-[#f4f8ff]">
          Разберите просадку метрики, найдите аномалию, сравните сегменты и проверьте гипотезу по данным в одном окне.
        </h3>

        <div className="mt-5 flex flex-wrap gap-2">
          <Badge variant="primary" className="gap-1.5 px-3 py-1.5">
            <ShieldCheck className="h-3.5 w-3.5" />
            Governed execution
          </Badge>
          <Badge variant="info" className="gap-1.5 px-3 py-1.5">
            <Database className="h-3.5 w-3.5" />
            Visible SQL
          </Badge>
        </div>

        <div className="mt-6 flex items-center gap-3">
          <Button type="button" variant="secondary" onClick={onOpenScenarioCatalog} className="gap-2">
            <Sparkles className="h-4 w-4" />
            Browse scenarios
          </Button>
          <span className="text-xs text-[#7f93b4]">{scenarioCount} scenarios ready</span>
        </div>
      </div>
    </motion.section>
  );
}
