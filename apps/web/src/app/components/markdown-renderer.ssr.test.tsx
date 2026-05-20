// @vitest-environment node

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "./markdown-renderer";

describe("MarkdownRenderer SSR", () => {
  it("renders safely during server-side rendering", () => {
    const html = renderToStaticMarkup(
      <MarkdownRenderer text={`# Title\n\n<script>alert(1)</script><a href="javascript:alert(1)">Unsafe</a>`} />
    );

    expect(html).toContain('<h1 class="docs-heading docs-heading-1"><a href="#title" class="docs-heading-anchor"');
    expect(html).toContain(">Title</a></h1>");
    expect(html).not.toContain("<script>");
    expect(html).toContain(">Unsafe</a>");
    expect(html).not.toContain("javascript:alert(1)");
  });
});
