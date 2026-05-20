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

// Type-to-color mapping
const TYPE_COLORS: Record<ClarificationChoiceType, { primary: string; secondary: string; border: string; hover: string }> = {
  choose_connector: {
    primary: "#5da8ff",
    secondary: "rgba(93, 168, 255, 0.12)",
    border: "rgba(93, 168, 255, 0.35)",
    hover: "rgba(93, 168, 255, 0.25)",
  },
  choose_table: {
    primary: "#38c67d",
    secondary: "rgba(56, 198, 125, 0.12)",
    border: "rgba(56, 198, 125, 0.35)",
    hover: "rgba(56, 198, 125, 0.25)",
  },
  choose_metric_definition: {
    primary: "#c084fc",
    secondary: "rgba(192, 132, 252, 0.12)",
    border: "rgba(192, 132, 252, 0.35)",
    hover: "rgba(192, 132, 252, 0.25)",
  },
  choose_time_range: {
    primary: "#fb923c",
    secondary: "rgba(251, 146, 60, 0.12)",
    border: "rgba(251, 146, 60, 0.35)",
    hover: "rgba(251, 146, 60, 0.25)",
  },
  confirm_assumption: {
    primary: "#fbbf24",
    secondary: "rgba(251, 191, 36, 0.12)",
    border: "rgba(251, 191, 36, 0.35)",
    hover: "rgba(251, 191, 36, 0.25)",
  },
  approve_expensive_query: {
    primary: "#f87171",
    secondary: "rgba(248, 113, 113, 0.12)",
    border: "rgba(248, 113, 113, 0.35)",
    hover: "rgba(248, 113, 113, 0.25)",
  },
};

// Type labels for accessibility
const TYPE_LABELS: Record<ClarificationChoiceType, string> = {
  choose_connector: "Select data source",
  choose_table: "Select table",
  choose_metric_definition: "Select metric definition",
  choose_time_range: "Select time range",
  confirm_assumption: "Confirm assumption",
  approve_expensive_query: "Approve expensive query",
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
        "focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-[#0a111d]",
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
          style={{ backgroundColor: `${colors.primary}20` }}
        >
          <Icon className="h-5 w-5" style={{ color: colors.primary }} />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4
              className="text-sm font-semibold"
              style={{ color: "#edf3ff" }}
            >
              {choice.label}
            </h4>
            {!disabled && (
              <div
                className="ml-auto rounded-full p-1 opacity-0 transition-opacity group-hover:opacity-100"
                style={{ backgroundColor: `${colors.primary}30` }}
              >
                <Check className="h-3 w-3" style={{ color: colors.primary }} />
              </div>
            )}
          </div>

          {/* Reason */}
          {choice.reason && (
            <p
              className="mt-1.5 text-xs leading-relaxed"
              style={{ color: "#a9b8d4" }}
            >
              {choice.reason}
            </p>
          )}

          {/* Provenance */}
          {choice.provenance_id && (
            <div className="mt-2 flex items-center gap-1.5">
              <Scale className="h-3 w-3" style={{ color: "#7484a3" }} />
              <span
                className="text-[10px] font-medium uppercase tracking-wider"
                style={{ color: "#7484a3" }}
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
  const typeLabel = TYPE_LABELS[clarification.type] ?? "Clarification";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="my-4 rounded-2xl border p-5"
      style={{
        backgroundColor: "linear-gradient(180deg, rgba(11,17,30,0.88), rgba(9,14,25,0.94))",
        background: "linear-gradient(180deg, rgba(14,21,38,0.95), rgba(11,17,28,0.98))",
        borderColor: colors.border,
        boxShadow: `0 8px 32px rgba(0, 0, 0, 0.4), inset 0 1px 0 ${colors.border}20`,
      }}
      data-testid="clarification-card"
    >
      {/* Header */}
      <div className="mb-4 flex items-center gap-3">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-lg"
          style={{ backgroundColor: `${colors.primary}20` }}
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
            style={{ color: "#edf3ff" }}
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
          style={{ backgroundColor: "rgba(46, 61, 89, 0.5)" }}
        />
        <span className="text-[10px] uppercase tracking-widest" style={{ color: "#7484a3" }}>
          Select one to continue
        </span>
        <div
          className="h-px flex-1"
          style={{ backgroundColor: "rgba(46, 61, 89, 0.5)" }}
        />
      </div>
    </motion.div>
  );
}
