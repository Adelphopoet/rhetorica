import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mermaidInitialize, mermaidRender } = vi.hoisted(() => ({
  mermaidInitialize: vi.fn(),
  mermaidRender: vi.fn(async (_id: string, definition: string) => ({
    svg: `<svg data-definition="${definition}"></svg>`,
  })),
}));

vi.mock("mermaid/dist/mermaid.esm.mjs", () => ({
  default: {
    initialize: mermaidInitialize,
    render: mermaidRender,
  },
}));

import { MarkdownRenderer } from "./markdown-renderer";

describe("MarkdownRenderer", () => {
  beforeEach(() => {
    mermaidInitialize.mockClear();
    mermaidRender.mockClear();
  });

  it("renders mermaid fences through the Mermaid client API", async () => {
    const { container } = render(
      <MarkdownRenderer
        text={["# Diagram", "", "```mermaid", "flowchart TD", "A --> B", "```"].join("\n")}
      />
    );

    await waitFor(() => {
      expect(mermaidInitialize).toHaveBeenCalledWith(
        expect.objectContaining({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
        })
      );
    });

    await waitFor(() => {
      expect(mermaidRender).toHaveBeenCalledWith(expect.any(String), "flowchart TD\nA --> B");
      expect(container.querySelector(".mermaid-diagram svg")).toBeInTheDocument();
    });
  });

  it("adds anchor ids to headings for docs navigation", () => {
    render(<MarkdownRenderer text={["# System Layers", "## Runtime"].join("\n")} />);

    expect(screen.getByRole("link", { name: "Link to System Layers" })).toHaveAttribute("href", "#system-layers");
    expect(screen.getByRole("link", { name: "Link to Runtime" })).toHaveAttribute("href", "#runtime");
  });

  it("keeps non-mermaid fenced code blocks as code blocks", () => {
    render(
      <MarkdownRenderer
        text={["```ts", "const result = 42;", "```"].join("\n")}
      />
    );

    expect(screen.getByText("const result = 42;", { selector: "code" })).toBeInTheDocument();
    expect(mermaidRender).not.toHaveBeenCalled();
  });

  it("sanitizes unsafe html payloads before rendering", () => {
    const { container } = render(
      <MarkdownRenderer text={`<script>window.hacked = true</script><a href="javascript:alert(1)">Unsafe</a>`} />
    );

    expect(container.querySelector("script")).not.toBeInTheDocument();
    const link = container.querySelector("a");
    expect(link).toHaveTextContent("Unsafe");
    expect(link).not.toHaveAttribute("href", "javascript:alert(1)");
  });
});
