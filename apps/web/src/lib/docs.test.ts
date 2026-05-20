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
    const page = await getDocsPage(["architecture", "architecture"]);

    expect(page).not.toBeNull();
    expect(page?.sourcePath).toMatch(/docs\/architecture\/architecture\.md$/);
    expect(page?.markdown).toContain("](/docs/architecture/diagrams/layers)");
    expect(page?.markdown).toContain("](/docs/architecture/diagrams/query_lifecycle)");
  });

  it("builds synthetic directory pages when a folder has no index.md", async () => {
    const page = await getDocsPage(["architecture", "diagrams"]);

    expect(page).not.toBeNull();
    expect(page?.markdown).toContain("[System Layers Diagram](/docs/architecture/diagrams/layers)");
    expect(page?.markdown).toContain("[Query Lifecycle](/docs/architecture/diagrams/query_lifecycle)");
  });

  it("extracts heading metadata for docs pages", async () => {
    const page = await getDocsPage(["architecture", "diagrams", "layers"]);

    expect(page).not.toBeNull();
    expect(page?.headings[0]).toMatchObject({
      level: 1,
      text: "System Layers Diagram",
    });
  });

  it("builds breadcrumbs and pager context from the navigation tree", async () => {
    const navItems = await getDocsNavigation();
    const breadcrumbs = getDocsBreadcrumbs(navItems, "/docs/architecture/diagrams/layers");
    const pager = getDocsPager(navItems, "/docs/architecture/diagrams/layers");

    expect(breadcrumbs.map((item) => item.title)).toEqual([
      "Documentation",
      "Architecture",
      "Diagrams",
      "System Layers Diagram",
    ]);
    expect(pager.previous?.href).toBe("/docs/architecture/diagrams/bounded_contexts");
    expect(pager.next?.href).toBe("/docs/architecture/diagrams/query_lifecycle");
    expect(pager.parent?.href).toBe("/docs/architecture/diagrams");
  });
});
