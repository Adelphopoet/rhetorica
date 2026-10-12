import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { getDocsBreadcrumbs, getDocsNavigation, getDocsPage, getDocsPager, rewriteMarkdownLinks } from "./docs";

describe("docs loader", () => {
  it("rewrites relative markdown links into /docs routes", () => {
    const rewritten = rewriteMarkdownLinks(
      "[Architecture](architecture/architecture.md)\n[Layers](architecture/diagrams/layers.md)",
      "index.md"
    );

    expect(rewritten).toContain("](/docs/architecture/architecture)");
    expect(rewritten).toContain("](/docs/architecture/diagrams/layers)");
  });

  it("loads docs pages from the repository docs tree", async () => {
    const page = await getDocsPage(["rhetorica", "dlya-polzovatelya", "quick-start"]);

    expect(page).not.toBeNull();
    expect(page?.sourcePath).toMatch(/docs\/rhetorica\/dlya-polzovatelya\/quick-start\/index\.md$/);
    expect(page?.markdown).toContain("## 1. Войдите в систему");
  });

  it("loads Docsy section pages from _index.md", async () => {
    const page = await getDocsPage(["rhetorica", "dlya-polzovatelya"]);

    expect(page).not.toBeNull();
    expect(page?.sourcePath).toMatch(/docs\/rhetorica\/dlya-polzovatelya\/_index\.md$/);
    expect(page?.markdown).toContain("# Для пользователя");
  });

  it("extracts heading metadata for docs pages", async () => {
    const page = await getDocsPage(["rhetorica", "dlya-polzovatelya", "quick-start"]);

    expect(page).not.toBeNull();
    expect(page?.headings[0]).toMatchObject({
      level: 1,
      text: "Быстрый старт",
    });
  });

  it("builds breadcrumbs and pager context from the navigation tree", async () => {
    const navItems = await getDocsNavigation();
    const href = "/docs/rhetorica/dlya-polzovatelya/quick-start";
    const breadcrumbs = getDocsBreadcrumbs(navItems, href);
    const pager = getDocsPager(navItems, href);

    expect(breadcrumbs.map((item) => item.title)).toEqual([
      "Документация",
      "Rhetorica",
      "Для пользователя",
      "Быстрый старт",
    ]);
    expect(pager.previous?.href).toBe("/docs/rhetorica/dlya-polzovatelya/api-mcp");
    expect(pager.next?.href).toBe("/docs/rhetorica/dlya-polzovatelya/results-files");
    expect(pager.parent?.href).toBe("/docs/rhetorica/dlya-polzovatelya");
  });
});
