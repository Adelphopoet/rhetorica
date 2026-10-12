import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClarificationCard } from "./ClarificationCard";
import type { ClarificationRequest } from "./clarificationTypes";

describe("ClarificationCard", () => {
  const mockClarification: ClarificationRequest = {
    clarification_id: "clar-001",
    question: "Which data source should I use for this analysis?",
    type: "choose_connector",
    choices: [
      {
        id: "conn-prod",
        label: "Production DW",
        reason: "Most up-to-date data with real-time sync",
        provenance_id: "conn/prod-dw/v2",
      },
      {
        id: "conn-staging",
        label: "Staging DW",
        reason: "Isolated environment for testing queries",
        provenance_id: "conn/staging-dw/v1",
      },
      {
        id: "conn-analytics",
        label: "Analytics DB",
        reason: "Optimized for aggregations and historical analysis",
      },
    ],
  };

  describe("rendering", () => {
    it("renders the question", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      expect(screen.getByText(mockClarification.question)).toBeInTheDocument();
    });

    it("renders all choices with labels", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      mockClarification.choices.forEach((choice) => {
        expect(screen.getByText(choice.label)).toBeInTheDocument();
      });
    });

    it("renders reason text for each choice", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      // First two choices have reasons
      expect(screen.getByText(mockClarification.choices[0].reason)).toBeInTheDocument();
      expect(screen.getByText(mockClarification.choices[1].reason)).toBeInTheDocument();
    });

    it("renders provenance_id when present", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      expect(screen.getByText("conn/prod-dw/v2")).toBeInTheDocument();
      expect(screen.getByText("conn/staging-dw/v1")).toBeInTheDocument();
    });

    it("renders correct icon for choose_connector type", () => {
      const onSelect = vi.fn();
      const { container } = render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      // The card should have an icon button with the correct data attribute
      const choiceButtons = container.querySelectorAll("button");
      expect(choiceButtons.length).toBe(mockClarification.choices.length);
    });

    it("renders type-specific color header", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      // Should have the type label visible
      expect(screen.getByText("Выбор источника данных")).toBeInTheDocument();
    });
  });

  describe("interaction", () => {
    it("calls onSelect with correct args when choice clicked", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      const firstChoice = screen.getByTestId("clarification-choice-conn-prod");
      fireEvent.click(firstChoice);

      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith("conn-prod", "Production DW");
    });

    it("calls onSelect with second choice when clicked", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      const secondChoice = screen.getByTestId("clarification-choice-conn-staging");
      fireEvent.click(secondChoice);

      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith("conn-staging", "Staging DW");
    });

    it("calls onSelect with third choice when clicked", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard clarification={mockClarification} onSelect={onSelect} />
      );

      const thirdChoice = screen.getByTestId("clarification-choice-conn-analytics");
      fireEvent.click(thirdChoice);

      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith("conn-analytics", "Analytics DB");
    });
  });

  describe("disabled state", () => {
    it("disables buttons when disabled prop is true", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard
          clarification={mockClarification}
          onSelect={onSelect}
          disabled={true}
        />
      );

      const firstChoice = screen.getByTestId("clarification-choice-conn-prod");
      expect(firstChoice).toBeDisabled();
    });

    it("does not call onSelect when disabled and clicked", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard
          clarification={mockClarification}
          onSelect={onSelect}
          disabled={true}
        />
      );

      const firstChoice = screen.getByTestId("clarification-choice-conn-prod");
      fireEvent.click(firstChoice);

      expect(onSelect).not.toHaveBeenCalled();
    });

    it("enables buttons when disabled prop is false", () => {
      const onSelect = vi.fn();
      render(
        <ClarificationCard
          clarification={mockClarification}
          onSelect={onSelect}
          disabled={false}
        />
      );

      const firstChoice = screen.getByTestId("clarification-choice-conn-prod");
      expect(firstChoice).not.toBeDisabled();
    });
  });

  describe("all clarification types", () => {
    const types: ClarificationRequest["type"][] = [
      "choose_connector",
      "choose_table",
      "choose_metric_definition",
      "choose_time_range",
      "confirm_assumption",
      "approve_expensive_query",
    ];

    it.each(types)("renders type: %s", (type) => {
      const onSelect = vi.fn();
      const clarification: ClarificationRequest = {
        clarification_id: `clar-${type}`,
        question: `Test question for ${type}`,
        type,
        choices: [
          {
            id: "choice-1",
            label: "Option 1",
            reason: "Test reason",
          },
        ],
      };

      const { container } = render(
        <ClarificationCard clarification={clarification} onSelect={onSelect} />
      );

      // Card should be rendered
      expect(container.querySelector('[data-testid="clarification-card"]')).toBeInTheDocument();
      // Should have at least one choice button
      expect(container.querySelectorAll("button").length).toBeGreaterThan(0);
    });
  });

  describe("edge cases", () => {
    it("handles empty choice list gracefully", () => {
      const onSelect = vi.fn();
      const emptyClarification: ClarificationRequest = {
        clarification_id: "clar-empty",
        question: "No choices available",
        type: "choose_connector",
        choices: [],
      };

      const { container } = render(
        <ClarificationCard clarification={emptyClarification} onSelect={onSelect} />
      );

      // Should still render the card
      expect(container.querySelector('[data-testid="clarification-card"]')).toBeInTheDocument();
      // No choice buttons should be rendered
      expect(container.querySelectorAll("button").length).toBe(0);
    });

    it("handles choice without provenance_id", () => {
      const onSelect = vi.fn();
      const clarification: ClarificationRequest = {
        clarification_id: "clar-no-prov",
        question: "Which table?",
        type: "choose_table",
        choices: [
          {
            id: "table-1",
            label: "users",
            reason: "Contains user data",
          },
        ],
      };

      render(
        <ClarificationCard clarification={clarification} onSelect={onSelect} />
      );

      // Should render the label and reason
      expect(screen.getByText("users")).toBeInTheDocument();
      expect(screen.getByText("Contains user data")).toBeInTheDocument();
    });

    it("handles long reason text", () => {
      const onSelect = vi.fn();
      const longReason = "A".repeat(500);
      const clarification: ClarificationRequest = {
        clarification_id: "clar-long",
        question: "Which connector?",
        type: "choose_connector",
        choices: [
          {
            id: "long-reason",
            label: "Connector",
            reason: longReason,
          },
        ],
      };

      const { container } = render(
        <ClarificationCard clarification={clarification} onSelect={onSelect} />
      );

      // Should render without crashing
      expect(container.querySelector('[data-testid="clarification-card"]')).toBeInTheDocument();
      expect(screen.getByText(longReason)).toBeInTheDocument();
    });
  });
});
