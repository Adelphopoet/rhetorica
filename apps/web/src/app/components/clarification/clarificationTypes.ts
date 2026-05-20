/**
 * Typed clarification request types matching backend contracts
 * Backend ref: packages/backend/src/rhetorica_backend/contracts/chat.py
 * - ClarificationChoiceType (enum)
 * - ClarificationChoice (model)
 * - ClarificationRequest (model)
 */

/**
 * Types of clarification requests that can be presented to the user.
 * Each type maps to a distinct visual treatment (color, icon).
 */
export type ClarificationChoiceType =
  | "choose_connector"
  | "choose_table"
  | "choose_metric_definition"
  | "choose_time_range"
  | "confirm_assumption"
  | "approve_expensive_query";

/**
 * Single choice option within a clarification request.
 */
export interface ClarificationChoice {
  /** Unique identifier for this choice */
  id: string;
  /** Display label shown to user */
  label: string;
  /** Explanation of why this choice is recommended */
  reason: string;
  /** Optional reference to the source/provenance */
  provenance_id?: string | null;
  /** Optional metadata for advanced use cases */
  metadata?: Record<string, unknown>;
}

/**
 * Typed clarification request presented to the user.
 * Contains a question and multiple choices the user can select from.
 */
export interface ClarificationRequest {
  /** Unique identifier for this clarification request */
  clarification_id: string;
  /** The question to present to the user */
  question: string;
  /** The type of clarification (determines visual treatment) */
  type: ClarificationChoiceType;
  /** Available choices for the user to select */
  choices: ClarificationChoice[];
}

/**
 * All valid clarification type values as a readonly array.
 * Useful for validation and testing.
 */
export const CLARIFICATION_TYPES: readonly ClarificationChoiceType[] = [
  "choose_connector",
  "choose_table",
  "choose_metric_definition",
  "choose_time_range",
  "confirm_assumption",
  "approve_expensive_query",
] as const;

/**
 * Type guard to check if a string is a valid ClarificationChoiceType
 */
export function isClarificationChoiceType(value: unknown): value is ClarificationChoiceType {
  return (
    typeof value === "string" &&
    CLARIFICATION_TYPES.includes(value as ClarificationChoiceType)
  );
}
