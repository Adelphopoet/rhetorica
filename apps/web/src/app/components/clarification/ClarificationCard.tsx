"use client";

import { motion } from "framer-motion";
import {
  ArrowLeftRight,
  Calendar,
  Check,
  Database,
  DollarSign,
  HelpCircle,
  Scale,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@rhetorica/ui";
import type {
  ClarificationChoiceType,
  ClarificationChoice,
  ClarificationRequest,
} from "./clarificationTypes";

export interface ClarificationCardProps {
  clarification: ClarificationRequest;
  onSelect: (choiceId: string, resolvedValue: string) => void;
  disabled?: boolean;
}

// Type-to-icon mapping
const TYPE_ICONS: Record<ClarificationChoiceType, LucideIcon> = {
  choose_connector: ArrowLeftRight,
  choose_table: Database,
  choose_metric_definition: Sparkles,
  choose_time_range: Calendar,
  confirm_assumption: HelpCircle,
  approve_expensive_query: DollarSign,
};

// Resolve UI roles through CSS so existing clarification cards update immediately.
function choiceColors(role: string) {
  return {
    primary: `rgb(var(--color-${role}))`,
    secondary: `rgb(var(--color-${role}) / 0.12)`,
    border: `rgb(var(--color-${role}) / 0.35)`,
    hover: `rgb(var(--color-${role}) / 0.25)`,
  };
}

const TYPE_COLORS: Record<ClarificationChoiceType, ReturnType<typeof choiceColors>> = {
  choose_connector: choiceColors("accent"),
  choose_table: choiceColors("success"),
  choose_metric_definition: choiceColors("purple"),
  choose_time_range: choiceColors("orange"),
  confirm_assumption: choiceColors("warning"),
  approve_expensive_query: choiceColors("danger"),
};

// Type labels for accessibility
const TYPE_LABELS: Record<ClarificationChoiceType, string> = {
  choose_connector: "Выбор источника данных",
  choose_table: "Выбор таблицы",
  choose_metric_definition: "Выбор определения метрики",
  choose_time_range: "Выбор периода",
  confirm_assumption: "Подтверждение гипотезы",
  approve_expensive_query: "Подтверждение дорогого запроса",
};

function getTypeColors(type: ClarificationChoiceType) {
  return TYPE_COLORS[type] ?? TYPE_COLORS.confirm_assumption;
}

/**
 * Single choice button within the clarification card
 */
function ChoiceButton({
  choice,
  type,
  onSelect,
  disabled,
}: {
  choice: ClarificationChoice;
  type: ClarificationChoiceType;
  onSelect: (choiceId: string, resolvedValue: string) => void;
  disabled?: boolean;
}) {
  const colors = getTypeColors(type);
  const Icon = TYPE_ICONS[type] ?? Settings;

  return (
    <motion.button
      type="button"
      disabled={disabled}
      whileHover={disabled ? {} : { scale: 1.01 }}
      whileTap={disabled ? {} : { scale: 0.98 }}
      onClick={() => onSelect(choice.id, choice.label)}
      className={cn(
        "w-full text-left rounded-xl border p-4 transition-all duration-200",
        "focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-border",
        disabled && "cursor-not-allowed opacity-50",
        !disabled && "cursor-pointer"
      )}
      style={{
        backgroundColor: colors.secondary,
        borderColor: colors.border,
      }}
      onMouseEnter={(e) => {
        if (!disabled) {
          e.currentTarget.style.backgroundColor = colors.hover;
          e.currentTarget.style.borderColor = colors.primary;
        }
      }}
      onMouseLeave={(e) => {
        if (!disabled) {
          e.currentTarget.style.backgroundColor = colors.secondary;
          e.currentTarget.style.borderColor = colors.border;
        }
      }}
      data-testid={`clarification-choice-${choice.id}`}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: colors.secondary }}
        >
          <Icon className="h-5 w-5" style={{ color: colors.primary }} />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4
              className="text-sm font-semibold"
              style={{ color: "rgb(var(--color-primary))" }}
            >
              {choice.label}
            </h4>
            {!disabled && (
              <div
                className="ml-auto rounded-full p-1 opacity-0 transition-opacity group-hover:opacity-100"
                style={{ backgroundColor: colors.hover }}
              >
                <Check className="h-3 w-3" style={{ color: colors.primary }} />
              </div>
            )}
          </div>

          {/* Reason */}
          {choice.reason && (
            <p
              className="mt-1.5 text-xs leading-relaxed"
              style={{ color: "rgb(var(--color-secondary))" }}
            >
              {choice.reason}
            </p>
          )}

          {/* Provenance */}
          {choice.provenance_id && (
            <div className="mt-2 flex items-center gap-1.5">
              <Scale className="h-3 w-3" style={{ color: "rgb(var(--color-muted))" }} />
              <span
                className="text-[10px] font-medium uppercase tracking-wider"
                style={{ color: "rgb(var(--color-muted))" }}
              >
                {choice.provenance_id}
              </span>
            </div>
          )}
        </div>
      </div>
    </motion.button>
  );
}

/**
 * ClarificationCard - Renders typed clarification requests from the backend
 *
 * Displays a question with multiple choice options. Each choice is visually
 * identified by the clarification type's color and icon.
 */
export function ClarificationCard({
  clarification,
  onSelect,
  disabled = false,
}: ClarificationCardProps) {
  const colors = getTypeColors(clarification.type);
  const Icon = TYPE_ICONS[clarification.type] ?? Settings;
  const typeLabel = TYPE_LABELS[clarification.type] ?? "Уточнение";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="my-4 rounded-2xl border p-5"
      style={{
        background: "linear-gradient(180deg, rgb(var(--color-surface)/0.95), rgb(var(--color-surface)/0.98))",
        borderColor: colors.border,
        boxShadow: `0 8px 32px rgb(var(--color-shadow)/calc(0.4*var(--shadow-opacity-scale))), inset 0 1px 0 ${colors.secondary}`,
      }}
      data-testid="clarification-card"
    >
      {/* Header */}
      <div className="mb-4 flex items-center gap-3">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-lg"
          style={{ backgroundColor: colors.secondary }}
        >
          <Icon className="h-4 w-4" style={{ color: colors.primary }} />
        </div>
        <div>
          <span
            className="text-[10px] font-semibold uppercase tracking-widest"
            style={{ color: colors.primary }}
          >
            {typeLabel}
          </span>
          <h3
            className="mt-0.5 text-base font-semibold leading-snug"
            style={{ color: "rgb(var(--color-primary))" }}
          >
            {clarification.question}
          </h3>
        </div>
      </div>

      {/* Choices */}
      <div
        className="space-y-2.5"
        role="radiogroup"
        aria-label={clarification.question}
      >
        {clarification.choices.map((choice, index) => (
          <motion.div
            key={choice.id}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{
              duration: 0.2,
              delay: index * 0.05,
              ease: "easeOut",
            }}
            className="group"
          >
            <ChoiceButton
              choice={choice}
              type={clarification.type}
              onSelect={onSelect}
              disabled={disabled}
            />
          </motion.div>
        ))}
      </div>

      {/* Footer hint */}
      <div className="mt-4 flex items-center gap-2">
        <div
          className="h-px flex-1"
          style={{ backgroundColor: "rgb(var(--color-border-strong)/0.5)" }}
        />
        <span className="text-[10px] uppercase tracking-widest" style={{ color: "rgb(var(--color-muted))" }}>
          Выберите один из вариантов, чтобы продолжить
        </span>
        <div
          className="h-px flex-1"
          style={{ backgroundColor: "rgb(var(--color-border-strong)/0.5)" }}
        />
      </div>
    </motion.div>
  );
}
