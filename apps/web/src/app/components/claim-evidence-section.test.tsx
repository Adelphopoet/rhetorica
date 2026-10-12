import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClaimEvidenceSection } from "./claim-evidence-section";
import type {
  AnswerClaim,
  AnswerEvidence,
  ClaimEvidenceLink,
} from "@/features/chat/streamingTypes";

// Mock lucide-react icons
vi.mock("lucide-react", () => ({
  Eye: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "eye-icon"}>Eye</span>
  ),
  CheckCircle: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "check-icon"}>CheckCircle</span>
  ),
  AlertTriangle: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "warning-icon"}>AlertTriangle</span>
  ),
  AlertCircle: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "alert-circle-icon"}>AlertCircle</span>
  ),
  Info: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "info-icon"}>Info</span>
  ),
  FileCode2: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "file-code-icon"}>FileCode2</span>
  ),
  Database: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "database-icon"}>Database</span>
  ),
  BookOpen: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "book-icon"}>BookOpen</span>
  ),
  ExternalLink: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "external-link-icon"}>ExternalLink</span>
  ),
  Gitlab: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "gitlab-icon"}>Gitlab</span>
  ),
  Search: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "search-icon"}>Search</span>
  ),
  Ticket: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "ticket-icon"}>Ticket</span>
  ),
  Sparkles: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "sparkles-icon"}>Sparkles</span>
  ),
  ChevronDown: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "chevron-down"}>ChevronDown</span>
  ),
  ChevronUp: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "chevron-up"}>ChevronUp</span>
  ),
  Copy: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "copy-icon"}>Copy</span>
  ),
  X: ({ "data-testid": testId }: { "data-testid"?: string }) => (
    <span data-testid={testId ?? "close-icon"}>X</span>
  ),
}));

describe("ClaimEvidenceSection", () => {
  const mockClaims: AnswerClaim[] = [
    {
      claim_id: "claim-1",
      text: "The total revenue increased by 15% in Q4 2024 compared to Q3 2024",
      status: "verified",
    },
    {
      claim_id: "claim-2",
      text: "North America had the highest sales growth at 22%",
      status: "unverified",
    },
  ];

  const mockEvidence: AnswerEvidence[] = [
    {
      evidence_id: "ev-1",
      type: "execution_result",
      reference: "sql_query_123",
      title: "Revenue by Quarter Query",
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
    {
      evidence_id: "ev-3",
      type: "rag_insight",
      reference: "kb_article_456",
      title: "Q4 2024 Financial Report",
      snippet:
        "The quarterly financial report shows significant growth in revenue",
      status: "unverified",
    },
  ];

  const mockLinks: ClaimEvidenceLink[] = [
    { claim_id: "claim-1", evidence_ids: ["ev-1", "ev-2"] },
    { claim_id: "claim-2", evidence_ids: ["ev-3"] },
  ];

  it("renders claims section header with count", () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    expect(screen.getByText(/^Утверждения$/i)).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // claim count badge
  });

  it("renders all claims with their status badges", () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // Verified claim
    expect(screen.getByText(/total revenue increased/i)).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();

    // Unverified claim
    expect(screen.getByText(/north america had the highest/i)).toBeInTheDocument();
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("shows evidence count badges for each claim", () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // claim-1 has 2 evidence items (1 SQL + 1 model) - use regex that matches the label container
    expect(screen.getByText(/1 SQL, 1 моделей/i)).toBeInTheDocument();

    // claim-2 has 1 evidence item (1 snippet)
    expect(screen.getByText(/1 фрагментов/i)).toBeInTheDocument();
  });

  it("applies warning visual treatment for unverified claims", () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // The unverified claim should have warning-tinted styling
    const unverifiedText = screen.getByText(/north america had the highest/i);
    const claimContainer = unverifiedText.closest("button");
    expect(claimContainer).toHaveClass(/border-warning|bg-warning/);
  });

  it("opens evidence inspector when claim is clicked", async () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // Click on the first claim
    const firstClaim = screen.getByText(/total revenue increased/i).closest(
      "button"
    );
    expect(firstClaim).toBeInTheDocument();
    fireEvent.click(firstClaim!);

    // Evidence inspector should be open - check for the title
    await screen.findByRole("heading", { name: /Инспектор доказательств/i });

    // The evidence should be displayed in the inspector (check for evidence titles)
    expect(screen.getByText("Revenue by Quarter Query")).toBeInTheDocument();
  });

  it("closes inspector when clicking the same claim again", () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // Initially no inspector should be open
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // Click first claim to open
    const firstClaim = screen.getByText(/total revenue increased/i).closest(
      "button"
    );
    fireEvent.click(firstClaim!);

    // Should have a dialog in the DOM
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // Click same claim to close (toggle behavior)
    fireEvent.click(firstClaim!);

    // The dialog should still be in DOM during animation exit
    // but with opacity 0 (exit animation)
    const closedDialog = screen.queryByRole("dialog");
    expect(closedDialog).toBeInTheDocument();
    expect(closedDialog).toHaveStyle({ opacity: 0 });
  });

  it("renders evidence inspector with linked evidence items", async () => {
    render(
      <ClaimEvidenceSection
        claims={mockClaims}
        evidence={mockEvidence}
        claimEvidenceLinks={mockLinks}
      />
    );

    // Click first claim
    const firstClaim = screen.getByText(/total revenue increased/i).closest(
      "button"
    );
    fireEvent.click(firstClaim!);

    // Should show evidence items
    await screen.findByText(/Доказательства$/i);
    expect(screen.getByText("SQL")).toBeInTheDocument();
    expect(screen.getByText("Модель метаданных")).toBeInTheDocument();
    expect(screen.getByText("Revenue by Quarter Query")).toBeInTheDocument();
    expect(screen.getByText("Sales Data Table")).toBeInTheDocument();
  });

  it("handles claims with no linked evidence", () => {
    const claimsWithNoEvidence: AnswerClaim[] = [
      {
        claim_id: "claim-no-ev",
        text: "This claim has no evidence attached",
        status: "unverified",
      },
    ];

    const linksWithNoEvidence: ClaimEvidenceLink[] = [];

    render(
      <ClaimEvidenceSection
        claims={claimsWithNoEvidence}
        evidence={[]}
        claimEvidenceLinks={linksWithNoEvidence}
      />
    );

    expect(
      screen.getByText(/this claim has no evidence attached/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/0 ссылок/i)).toBeInTheDocument();
  });

  it("handles empty claims array gracefully", () => {
    render(
      <ClaimEvidenceSection claims={[]} evidence={[]} claimEvidenceLinks={[]} />
    );

    // Should render but show 0 claims
    expect(screen.getByText(/^Утверждения$/i)).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("handles duplicate claim ids without colliding React keys or selection state", async () => {
    const duplicateClaims: AnswerClaim[] = [
      {
        claim_id: "},{",
        text: "First duplicate claim",
        status: "verified",
      },
      {
        claim_id: "},{",
        text: "Second duplicate claim",
        status: "unverified",
      },
    ];
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    try {
      render(
        <ClaimEvidenceSection
          claims={duplicateClaims}
          evidence={[]}
          claimEvidenceLinks={[]}
        />
      );

      expect(
        consoleErrorSpy.mock.calls.some((call) =>
          call.some((value) =>
            String(value).includes("Encountered two children with the same key")
          )
        )
      ).toBe(false);

      const secondClaim = screen.getByText(/second duplicate claim/i).closest(
        "button"
      );
      fireEvent.click(secondClaim!);

      await screen.findByRole("heading", { name: /Инспектор доказательств/i });
      expect(screen.getAllByText(/second duplicate claim/i)).toHaveLength(2);
      expect(screen.getByText(/доказательства не привязаны/i)).toBeInTheDocument();
    } finally {
      consoleErrorSpy.mockRestore();
    }
  });

  it("handles evidence with missing metadata", async () => {
    const evidenceWithMissingMeta: AnswerEvidence[] = [
      {
        evidence_id: "ev-missing",
        type: "execution_result",
        reference: "query_id_999",
        title: null,
        snippet: null,
        status: "unverified",
      },
    ];

    const links: ClaimEvidenceLink[] = [
      { claim_id: "claim-1", evidence_ids: ["ev-missing"] },
    ];

    render(
      <ClaimEvidenceSection
        claims={mockClaims.slice(0, 1)}
        evidence={evidenceWithMissingMeta}
        claimEvidenceLinks={links}
      />
    );

    // Click claim to open inspector
    const firstClaim = screen.getByText(/total revenue increased/i).closest(
      "button"
    );
    fireEvent.click(firstClaim!);

    // Should show metadata unavailable message
    await screen.findByText(/Метаданные недоступны/i);
  });

  it("correctly maps evidence types to summary labels", () => {
    const mixedEvidence: AnswerEvidence[] = [
      { evidence_id: "ev1", type: "execution_result", reference: "ref1", status: "verified" },
      { evidence_id: "ev2", type: "execution_result", reference: "ref2", status: "verified" },
      { evidence_id: "ev3", type: "metadata_entity", reference: "ref3", status: "verified" },
      { evidence_id: "ev4", type: "rag_insight", reference: "ref4", status: "verified" },
      { evidence_id: "ev5", type: "internal_documentation", reference: "ref5", status: "verified" },
      { evidence_id: "ev6", type: "gitlab_file", reference: "ref6", status: "verified" },
    ];

    const link: ClaimEvidenceLink[] = [
      {
        claim_id: "claim-1",
        evidence_ids: ["ev1", "ev2", "ev3", "ev4", "ev5", "ev6"],
      },
    ];

    render(
      <ClaimEvidenceSection
        claims={mockClaims.slice(0, 1)}
        evidence={mixedEvidence}
        claimEvidenceLinks={link}
      />
    );

    // Should show correct counts
    expect(screen.getByText(/2 SQL/i)).toBeInTheDocument();
    expect(screen.getByText(/1 моделей/i)).toBeInTheDocument();
    expect(screen.getByText(/1 фрагментов/i)).toBeInTheDocument();
    expect(screen.getByText(/1 документов/i)).toBeInTheDocument();
    expect(screen.getByText(/1 файлов GitLab/i)).toBeInTheDocument();
  });
});
