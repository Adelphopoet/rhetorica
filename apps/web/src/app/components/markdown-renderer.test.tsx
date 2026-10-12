import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const currentTheme = vi.hoisted(() => ({ resolvedTheme: "dark" as "dark" | "light" }));
vi.mock("@/features/theme/ThemeProvider", () => ({ useTheme: () => currentTheme }));

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
    currentTheme.resolvedTheme = "dark";
    mermaidInitialize.mockClear();
    mermaidRender.mockReset().mockImplementation(async (_id: string, definition: string) => ({ svg: `<svg data-definition="${definition}"></svg>` }));
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

    expect(screen.getByRole("link", { name: "Ссылка на System Layers" })).toHaveAttribute("href", "#system-layers");
    expect(screen.getByRole("link", { name: "Ссылка на Runtime" })).toHaveAttribute("href", "#runtime");
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

  it("hides visualization protocol JSON blocks from assistant markdown", () => {
    render(
      <MarkdownRenderer
        text={[
          "## Orders",
          "",
          "```json",
          '{"visualization":{"version":"v2","kind":"bar","data_source":{"execution_id":"exec-1"},"encoding":{"x":"day","y":["orders"]}}}',
          "```",
          "",
          "Chart rendered below",
        ].join("\n")}
      />
    );

    expect(screen.getByRole("heading", { name: "Ссылка на Orders" })).toBeInTheDocument();
    expect(screen.getByText("Chart rendered below")).toBeInTheDocument();
    expect(screen.queryByText(/"visualization"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/execution_id/)).not.toBeInTheDocument();
  });

  it("keeps ordinary json code blocks visible", () => {
    render(
      <MarkdownRenderer
        text={["```json", '{"status":"ok","rows":2}', "```"].join("\n")}
      />
    );

    expect(screen.getByText('{"status":"ok","rows":2}', { selector: "code" })).toBeInTheDocument();
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
  it("redraws existing diagrams in both themes without changing the definition", async () => {
    const text = "```mermaid\nflowchart TD\nA --> B\n```";
    const view = render(<MarkdownRenderer text={text} />);
    await waitFor(() => expect(view.container.querySelector("svg")).toBeInTheDocument());
    const firstId = mermaidRender.mock.calls[0][0];
    for (const theme of ["light", "dark"] as const) {
      currentTheme.resolvedTheme = theme;
      view.rerender(<MarkdownRenderer text={text} />);
      await waitFor(() => expect(view.container.querySelector('[data-mermaid-rendered="true"]')).toBeInTheDocument());
      expect(mermaidInitialize).toHaveBeenLastCalledWith(expect.objectContaining({ darkMode: theme === "dark", themeVariables: expect.objectContaining({ background: theme === "light" ? "#ffffff" : "#08111d" }) }));
      expect(mermaidRender).toHaveBeenLastCalledWith(expect.not.stringMatching(new RegExp(`^${firstId}$`)), "flowchart TD\nA --> B");
    }
    expect(mermaidRender).toHaveBeenCalledTimes(3);
  });

  it("serializes themes and ignores an old successful render after rapid switches", async () => {
    let finishOld!: (result: { svg: string }) => void;
    mermaidRender.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    const text = "```mermaid\nflowchart TD\nA --> B\n```";
    const view = render(<MarkdownRenderer text={text} />);
    await waitFor(() => expect(mermaidRender).toHaveBeenCalledTimes(1));
    currentTheme.resolvedTheme = "light";
    view.rerender(<MarkdownRenderer text={text} />);
    currentTheme.resolvedTheme = "dark";
    view.rerender(<MarkdownRenderer text={text} />);
    currentTheme.resolvedTheme = "light";
    view.rerender(<MarkdownRenderer text={text} />);
    expect(mermaidInitialize).toHaveBeenCalledTimes(1);
    await act(async () => finishOld({ svg: '<svg data-stale="true"></svg>' }));
    await waitFor(() => expect(view.container.querySelector('[data-mermaid-rendered="true"]')).toBeInTheDocument());
    expect(view.container.querySelector('[data-stale="true"]')).not.toBeInTheDocument();
    expect(mermaidRender).toHaveBeenCalledTimes(2);
    expect(mermaidInitialize).toHaveBeenLastCalledWith(expect.objectContaining({ darkMode: false }));
  });

  it("ignores stale failures, releases the queue and renders the new theme", async () => {
    let rejectOld!: (reason: Error) => void;
    mermaidRender.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
    const text = "```mermaid\nflowchart TD\nA --> B\n```";
    const view = render(<MarkdownRenderer text={text} />);
    await waitFor(() => expect(mermaidRender).toHaveBeenCalledTimes(1));
    currentTheme.resolvedTheme = "light";
    view.rerender(<MarkdownRenderer text={text} />);
    await act(async () => rejectOld(new Error("old render failed")));
    await waitFor(() => expect(view.container.querySelector('[data-mermaid-rendered="true"]')).toBeInTheDocument());
    expect(view.container.querySelector('[data-mermaid-rendered="error"]')).not.toBeInTheDocument();
    expect(mermaidInitialize).toHaveBeenLastCalledWith(expect.objectContaining({ darkMode: false }));
  });

  it.each(["dark", "light"] as const)("shows a safe code fallback and continues to the next diagram in %s", async theme => {
    currentTheme.resolvedTheme = theme;
    mermaidRender.mockRejectedValueOnce(new Error("invalid diagram"));
    const { container } = render(<MarkdownRenderer text={'```mermaid\n<img src=x onerror=alert(1)>\n```\n\n```mermaid\nflowchart TD\nA --> B\n```'} />);
    await waitFor(() => expect(container.querySelectorAll('[data-mermaid-rendered="true"]')).toHaveLength(1));
    expect(container.querySelector('[data-mermaid-rendered="error"] code')).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(mermaidRender).toHaveBeenCalledTimes(2);
  });

  it("does not bind or insert diagrams after unmount", async () => {
    let finish!: (result: { svg: string; bindFunctions: () => void }) => void;
    const bindFunctions = vi.fn();
    mermaidRender.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<MarkdownRenderer text={"```mermaid\nflowchart TD\nA --> B\n```"} />);
    await waitFor(() => expect(mermaidRender).toHaveBeenCalledTimes(1));
    view.unmount();
    await act(async () => finish({ svg: "<svg></svg>", bindFunctions }));
    expect(bindFunctions).not.toHaveBeenCalled();
  });

});
