"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Button, cn } from "@rhetorica/ui";
import { Compass, Database, Diamond, ShieldCheck, Sparkles } from "lucide-react";
import { useAgentMenuStore } from "@/features/agents/agentMenuStore";
import { useGuidesPanelStore } from "@/features/guides/guidesPanelStore";
import type { ChatWidth } from "./chat-tools";
import { startAppTour } from "./app-tour";

type ChatEmptyStateProps = {
  chatWidth: ChatWidth;
  onOpenScenarioCatalog: () => void;
};

export function ChatEmptyState({
  chatWidth,
  onOpenScenarioCatalog,
}: ChatEmptyStateProps) {
  const shouldReduceMotion = useReducedMotion();
  const toggleAgentMenu = useAgentMenuStore((state) => state.toggleAgentMenuWithPulse);
  const openGuidesPanel = useGuidesPanelStore((state) => state.openGuidesPanel);

  return (
    <motion.section
      initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 18 }}
      animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="flex h-full min-h-[260px] items-center justify-center"
    >
      <div
        data-testid="chat-empty-state-card"
        className={cn(
          "ce-container w-full overflow-hidden rounded-[28px] border border-border bg-[radial-gradient(circle_at_top_left,rgb(var(--color-accent)/0.12),transparent_28%),linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] p-6 shadow-[0_24px_70px_rgb(var(--color-shadow)/calc(0.3*var(--shadow-opacity-scale)))] sm:p-7",
          chatWidth === 1100 && "max-w-[1100px]",
          chatWidth === 1600 && "max-w-[1600px]"
        )}
      >
        <div className="flex items-center gap-6">
          <Sparkles className="ce-sparkles h-36 w-36 shrink-0 px-3 text-accent/80 drop-shadow-[0_0_32px_rgb(var(--color-accent)/0.5)]" strokeWidth={0.75} />
          <div className="min-w-0">
            <p className="pl-1 text-sm font-semibold uppercase tracking-[0.22em] text-muted">
              Твой AI аналитик
            </p>
            <span className="ce-rhetorica mt-2 block bg-gradient-to-r from-accent via-accent/80 to-[var(--brand-text-end)] bg-clip-text font-semibold leading-none tracking-[0.02em] text-transparent">
              Rhetorica
            </span>
            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-secondary">
              <span className="flex shrink-0 items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-accent" />
                Безопасное выполнение запросов
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <Database className="h-4 w-4 text-cyan" />
                Прозрачный SQL
              </span>
            </div>
          </div>
        </div>
        <h3 className="mt-12 text-md font-light text-primary">
          Разберите просадку метрики, найдите аномалию, сравните сегменты и проверьте гипотезу по данным в одном окне
        </h3>

        <div className="mt-4 flex pb-1">
          <Button
            type="button"
            variant="secondary"
            onClick={startAppTour}
            className="mt-1 gap-2"
          >
            <Compass className="h-4 w-4" />
            Совершить тур по Rhetorica
          </Button>
        </div>

        <div className="mt-12 flex flex-wrap items-start gap-x-12 gap-y-8">
          <div className="flex min-w-[300px] flex-1 flex-col items-start gap-3">
            <p className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.22em] text-muted">
              <Diamond className="h-2.5 w-2.5 shrink-0 fill-current text-accent" />
              Сценарии
            </p>
            <p className="text-base text-secondary">
              Используйте сценарии и повторяйте анализ в один клик
            </p>
            <Button
              type="button"
              variant="secondary"
              data-tour="tour-scenario-catalog"
              onClick={onOpenScenarioCatalog}
              className="mt-1 gap-2"
            >
              <Sparkles className="h-4 w-4" />
              Сценарии
            </Button>
          </div>

          <div className="flex min-w-[300px] flex-1 flex-col items-start gap-3">
            <p className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.22em] text-muted">
              <Diamond className="h-2.5 w-2.5 shrink-0 fill-current text-accent" />
              Агенты
            </p>
            <p className="text-base text-secondary">
              Выберите агента под задачу — ответ будет точнее под ваш контекст
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={toggleAgentMenu}
              className="mt-1 gap-2"
            >
              <Sparkles className="h-4 w-4" />
              Выбрать агента
            </Button>
          </div>

          <div className="flex min-w-[300px] flex-1 flex-col items-start gap-3">
            <p className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.22em] text-muted">
              <Diamond className="h-2.5 w-2.5 shrink-0 fill-current text-accent" />
              Полезные инструкции
            </p>
            <p className="text-base text-secondary">
              Найдите полезную информацию для решения задач
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={openGuidesPanel}
              className="mt-1 gap-2"
            >
              <Sparkles className="h-4 w-4" />
              Показать
            </Button>
          </div>
        </div>
      </div>
    </motion.section>
  );
}
