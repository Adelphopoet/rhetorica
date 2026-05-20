import { describe, expect, it } from "vitest";

import { extractMarkdownHeadings, slugifyMarkdownHeading } from "./markdown";

describe("markdown helpers", () => {
  it("builds stable slugs for headings", () => {
    expect(slugifyMarkdownHeading("Bounded Contexts")).toBe("bounded-contexts");
    expect(slugifyMarkdownHeading("Слои интеграции")).toBe("слои-интеграции");
  });

  it("extracts headings and skips fenced code blocks", () => {
    const headings = extractMarkdownHeadings([
      "# Overview",
      "## Runtime",
      "```md",
      "## Fake heading",
      "```",
      "## Runtime",
    ].join("\n"));

    expect(headings).toEqual([
      { id: "overview", level: 1, text: "Overview" },
      { id: "runtime", level: 2, text: "Runtime" },
      { id: "runtime-2", level: 2, text: "Runtime" },
    ]);
  });
});
