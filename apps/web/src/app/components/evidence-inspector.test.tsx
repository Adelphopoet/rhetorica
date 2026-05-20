import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EvidenceInspector } from "./evidence-inspector";
import type { AnswerClaim, AnswerEvidence } from "@/features/chat/streamingTypes";

// Mock lucide-react icons
vi.mock("lucide-react", () => ({
  FileCode2: () => <span data-testid="file-code-icon">FileCode2</span>,
  Database: () => <span data-testid="database-icon">Database</span>,
  BookOpen: () => <span data-testid="book-icon">BookOpen</span>,
  ExternalLink: () => <span data-testid="external-link-icon">ExternalLink</span>,
  AlertTriangle: () => <span data-testid="alert-icon">AlertTriangle</span>,
  CheckCircle: () => <span data-testid="check-icon">CheckCircle</span>,
  ChevronDown: () => <span data-testid="chevron-down">ChevronDown</span>,
  ChevronUp: () => <span data-testid="chevron-up">ChevronUp</span>,
  Copy: () => <span data-testid="copy-icon">Copy</span>,
  Sparkles: () => <span data-testid="sparkles-icon">Sparkles</span>,
  Info: () => <span data-testid="info-icon">Info</span>,
  AlertCircle: () => <span data-testid="alert-circle-icon">AlertCircle</span>,
  X: () => <span data-testid="close-icon">X</span>,
}));

describe("EvidenceInspector", () => {
  const mockClaim: AnswerClaim = {
    claim_id: "claim-1",
    text: "The total revenue increased by 15% in Q4 2024.",
    status: "verified",
  };

  const mockEvidence: AnswerEvidence[] = [
    {
      evidence_id: "ev-1",
      type: "execution_result",
      reference: "sql_query_123",
      title: "Revenue Query",
      snippet: "SELECT quarter, SUM(revenue) FROM sales GROUP BY quarter",
      status: "verified",
    },
    {
      evidence_id: "ev-2",
      type: "metadata_entity",
      reference: "table:sales_data",
      title: "Sales Data Table",
      snippet: null,
      status: "verified",
    },
  ];

  const mockOnClose = vi.fn();

  beforeEach(() => {
    mockOnClose.mockClear();
  });

  describe("rendering", () => {
    it("renders nothing when closed", () => {
      render(
        <EvidenceInspector
          open={false}
          onClose={mockOnClose}
          claim={null}
          evidence={[]}
        />
      );

      expect(screen.queryByText(/Evidence Inspector/i)).not.toBeInTheDocument();
    });

    it("renders sheet when open with claim and evidence", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      expect(screen.getByText(/Evidence Inspector/i)).toBeInTheDocument();
      expect(screen.getByText(mockClaim.text)).toBeInTheDocument();
    });

    it("shows correct title and description", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      expect(screen.getByRole("heading", { name: /Evidence Inspector/i })).toBeInTheDocument();
    });

    it("shows empty state when no claim is selected", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={null}
          evidence={[]}
        />
      );

      expect(screen.getByText(/Select a claim to view its evidence/i)).toBeInTheDocument();
    });
  });

  describe("evidence type icons", () => {
    it("renders execution_result type with FileCode2 icon", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      // Use getAllBy since icon appears in multiple places (evidence item)
      const fileCodeIcons = screen.getAllByTestId("file-code-icon");
      expect(fileCodeIcons.length).toBeGreaterThan(0);
      expect(screen.getByText("SQL")).toBeInTheDocument();
    });

    it("renders metadata_entity type with Database icon", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[1]]}
        />
      );

      const dbIcons = screen.getAllByTestId("database-icon");
      expect(dbIcons.length).toBeGreaterThan(0);
      expect(screen.getByText("Metadata model")).toBeInTheDocument();
    });

    it("renders rag_insight type with BookOpen icon", () => {
      const ragEvidence: AnswerEvidence = {
        evidence_id: "ev-rag",
        type: "rag_insight",
        reference: "kb_123",
        title: "Knowledge Base Article",
        snippet: "Some knowledge snippet",
        status: "verified",
      };

      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[ragEvidence]}
        />
      );

      const bookIcons = screen.getAllByTestId("book-icon");
      expect(bookIcons.length).toBeGreaterThan(0);
      expect(screen.getByText("Knowledge snippet")).toBeInTheDocument();
    });

    it("renders confluence_page type with ExternalLink icon", () => {
      const confluenceEvidence: AnswerEvidence = {
        evidence_id: "ev-conf",
        type: "confluence_page",
        reference: "https://confluence.example.com/page/123",
        title: "Confluence Documentation",
        snippet: "Documentation content",
        status: "verified",
      };

      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[confluenceEvidence]}
        />
      );

      // There are multiple ExternalLink icons (one for the evidence item, one for the link)
      const linkIcons = screen.getAllByTestId("external-link-icon");
      expect(linkIcons.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("Confluence")).toBeInTheDocument();
    });
  });

  describe("claim status display", () => {
    it("shows verified status with success badge", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      // There are multiple "verified" badges (one for claim, one for each evidence)
      const verifiedBadges = screen.getAllByText("verified");
      expect(verifiedBadges.length).toBeGreaterThan(0);
      // There are multiple check icons as well
      const checkIcons = screen.getAllByTestId("check-icon");
      expect(checkIcons.length).toBeGreaterThan(0);
    });

    it("shows unverified status with warning badge", () => {
      const unverifiedClaim: AnswerClaim = {
        ...mockClaim,
        status: "unverified",
      };

      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={unverifiedClaim}
          evidence={mockEvidence}
        />
      );

      expect(screen.getByText("unverified")).toBeInTheDocument();
      expect(screen.getByTestId("alert-icon")).toBeInTheDocument();
    });
  });

  describe("evidence items", () => {
    it("displays evidence title when available", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      expect(screen.getByText("Revenue Query")).toBeInTheDocument();
      expect(screen.getByText("Sales Data Table")).toBeInTheDocument();
    });

    it("displays evidence snippet when available", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      // The SQL appears in blockquote format
      expect(
        screen.getByText(/SELECT quarter, SUM\(revenue\)/i)
      ).toBeInTheDocument();
    });

    it("shows metadata unavailable for evidence without title/snippet", () => {
      const evidenceWithoutMeta: AnswerEvidence = {
        evidence_id: "ev-no-meta",
        type: "execution_result",
        reference: "query_999",
        title: null,
        snippet: null,
        status: "unverified",
      };

      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[evidenceWithoutMeta]}
        />
      );

      expect(screen.getByText("Metadata unavailable")).toBeInTheDocument();
    });

    it("displays clickable reference link for URLs", () => {
      const evidenceWithUrl: AnswerEvidence = {
        evidence_id: "ev-url",
        type: "confluence_page",
        reference: "https://confluence.example.com/page/123",
        title: "Confluence Page",
        status: "verified",
      };

      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[evidenceWithUrl]}
        />
      );

      const link = screen.getByRole("link", {
        name: /confluence\.example\.com/i,
      });
      expect(link).toHaveAttribute("href", "https://confluence.example.com/page/123");
      expect(link).toHaveAttribute("target", "_blank");
    });

    it("displays non-URL references as plain text", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      expect(screen.getByText("sql_query_123")).toBeInTheDocument();
    });
  });

  describe("no evidence linked state", () => {
    it("shows warning message when claim has no linked evidence", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[]}
        />
      );

      expect(screen.getByText("No evidence linked")).toBeInTheDocument();
      expect(
        screen.getByText(/This claim has no associated evidence items/i)
      ).toBeInTheDocument();
    });
  });

  describe("close functionality", () => {
    it("calls onClose when close button is clicked", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      const closeButton = screen.getByRole("button", { name: /close panel/i });
      fireEvent.click(closeButton);

      expect(mockOnClose).toHaveBeenCalledTimes(1);
    });

    it("calls onClose when backdrop is clicked", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      // The backdrop is a motion.div, need to find it differently
      const backdrop = document.querySelector('[class*="fixed inset-0"]');
      if (backdrop) {
        fireEvent.click(backdrop);
      }

      expect(mockOnClose).toHaveBeenCalledTimes(1);
    });
  });

  describe("execution result preview", () => {
    it("shows collapsible SQL preview for execution_result type", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      // Should show "Expand" button
      expect(screen.getByText("Expand")).toBeInTheDocument();
    });

    it("expands SQL preview when expand button is clicked", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      const expandButton = screen.getByText("Expand");
      fireEvent.click(expandButton);

      // Should now show Collapse button
      expect(screen.getByText("Collapse")).toBeInTheDocument();
      // The SQL code block (pre tag) should be visible in the expandable section
      const preElements = document.querySelectorAll("pre");
      expect(preElements.length).toBeGreaterThan(0);
    });

    it("collapses SQL preview when collapse button is clicked", () => {
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={[mockEvidence[0]]}
        />
      );

      // First expand
      fireEvent.click(screen.getByText("Expand"));

      // Then collapse
      fireEvent.click(screen.getByText("Collapse"));

      // SQL code block (pre tag) should be hidden in the expandable section
      // Note: The blockquote still shows the snippet as the evidence description
      const preElements = document.querySelectorAll("pre");
      const visiblePre = Array.from(preElements).find(
        (pre) => pre.closest('[class*="rounded-lg border border-[#22314d]"]')
      );
      expect(visiblePre).toBeUndefined();
    });
  });

  describe("mobile layout", () => {
    it("renders full-width on mobile (sheet handles this natively)", () => {
      // The Sheet component uses w-full, which handles mobile natively
      render(
        <EvidenceInspector
          open={true}
          onClose={mockOnClose}
          claim={mockClaim}
          evidence={mockEvidence}
        />
      );

      // Sheet should render
      const sheet = document.querySelector('[role="dialog"]');
      expect(sheet).toBeInTheDocument();
    });
  });
});
