import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

import { extractMarkdownHeadings, type MarkdownHeading } from "@/lib/markdown";

export interface DocsNavItem {
  title: string;
  href: string;
  children: DocsNavItem[];
}

export interface DocsPagerLink {
  title: string;
  href: string;
}

export interface DocsPageData {
  title: string;
  href: string;
  markdown: string;
  sourcePath: string;
  headings: MarkdownHeading[];
}

async function resolveDocsRoot() {
  for (const candidate of [path.resolve(process.cwd(), "docs"), path.resolve(process.cwd(), "../../docs")]) {
    try {
      const stats = await fs.stat(candidate);
      if (stats.isDirectory()) {
        return candidate;
      }
    } catch {
      continue;
    }
  }
  throw new Error("Docs root is not available from apps/web.");
}

function toTitleCase(value: string) {
  return value
    .replace(/[-_]+/g, " ")
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function slugToHref(relativePath: string) {
  if (!relativePath || relativePath === "index") {
    return "/docs";
  }

  const normalized = relativePath.replace(/\/index$/, "").replace(/\.md$/, "");
  return normalized ? `/docs/${normalized}` : "/docs";
}

function sanitizeRelativePath(relativePath: string) {
  const normalized = path.posix.normalize(relativePath).replace(/^\/+/, "");
  if (normalized.startsWith("../")) {
    return null;
  }
  return normalized === "." ? "" : normalized;
}

async function readMarkdownTitle(filePath: string, fallback: string) {
  const content = await fs.readFile(filePath, "utf-8");
  const headingMatch = content.match(/^#\s+(.+)$/m);
  return headingMatch?.[1]?.trim() || fallback;
}

async function readMarkdownTitleIfExists(filePath: string, fallback: string) {
  try {
    const stats = await fs.stat(filePath);
    if (!stats.isFile()) {
      return fallback;
    }
  } catch {
    return fallback;
  }

  return readMarkdownTitle(filePath, fallback);
}

async function buildNavigationTree(directoryPath: string, relativeDirectory = ""): Promise<DocsNavItem[]> {
  const entries = await fs.readdir(directoryPath, { withFileTypes: true });
  const items: DocsNavItem[] = [];

  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    if (entry.name.startsWith(".")) {
      continue;
    }

    const absolutePath = path.join(directoryPath, entry.name);
    const relativePath = path.posix.join(relativeDirectory, entry.name);

    if (entry.isDirectory()) {
      const children = await buildNavigationTree(absolutePath, relativePath);
      const indexPath = path.join(absolutePath, "index.md");
      const title = await readMarkdownTitleIfExists(indexPath, toTitleCase(entry.name));

      items.push({
        title,
        href: slugToHref(relativePath),
        children,
      });
      continue;
    }

    if (!entry.isFile() || !entry.name.endsWith(".md")) {
      continue;
    }
    if (entry.name === "index.md") {
      continue;
    }

    const fileRelativePath = relativePath.replace(/\\/g, "/");
    const fallbackTitle = toTitleCase(entry.name.replace(/\.md$/, ""));

    items.push({
      title: await readMarkdownTitle(absolutePath, fallbackTitle),
      href: slugToHref(fileRelativePath.replace(/\.md$/, "")),
      children: [],
    });
  }

  return items.filter((item) => item.href !== "/docs/index");
}

function splitLinkTarget(target: string) {
  const [pathname, hash = ""] = target.split("#", 2);
  return {
    pathname,
    hash: hash ? `#${hash}` : "",
  };
}

export function rewriteMarkdownLinks(markdown: string, currentDocRelativePath: string) {
  const currentDirectory = path.posix.dirname(currentDocRelativePath);

  return markdown.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (fullMatch, label: string, rawTarget: string) => {
    const target = rawTarget.trim();
    if (!target || /^(https?:\/\/|mailto:|#)/i.test(target)) {
      return fullMatch;
    }

    const { pathname: targetPath, hash } = splitLinkTarget(target);
    if (targetPath.startsWith("/docs/")) {
      return `[${label}](${slugToHref(targetPath.replace(/^\/docs\//, ""))}${hash})`;
    }

    const resolvedRelativePath = sanitizeRelativePath(path.posix.join(currentDirectory, targetPath));
    if (resolvedRelativePath == null) {
      return fullMatch;
    }
    return `[${label}](${slugToHref(resolvedRelativePath)}${hash})`;
  });
}

async function buildDirectoryListing(relativeDirectory: string, navItems: DocsNavItem[]) {
  const title = relativeDirectory ? toTitleCase(path.basename(relativeDirectory)) : "Documentation";
  const listing = navItems
    .map((item) => `- [${item.title}](${item.href})`)
    .join("\n");

  return {
    title,
    href: slugToHref(relativeDirectory),
    sourcePath: path.join(await resolveDocsRoot(), relativeDirectory),
    markdown: `# ${title}\n\n${listing}`,
    headings: [{ id: "documentation", level: 1, text: title }],
  } satisfies DocsPageData;
}

function flattenNavigation(items: DocsNavItem[]): DocsPagerLink[] {
  return items.flatMap((item) => [item, ...flattenNavigation(item.children)]);
}

function findTrail(items: DocsNavItem[], href: string, parents: DocsNavItem[] = []): DocsNavItem[] | null {
  for (const item of items) {
    const nextParents = [...parents, item];
    if (item.href === href) {
      return nextParents;
    }
    if (item.children.length > 0) {
      const nestedMatch = findTrail(item.children, href, nextParents);
      if (nestedMatch) {
        return nestedMatch;
      }
    }
  }
  return null;
}

export async function getDocsNavigation() {
  const docsRoot = await resolveDocsRoot();
  const nav = await buildNavigationTree(docsRoot);
  return nav.filter((item) => item.href !== "/docs");
}

export function getDocsBreadcrumbs(navItems: DocsNavItem[], href: string): DocsPagerLink[] {
  const trail = findTrail(navItems, href) ?? [];
  return [{ title: "Documentation", href: "/docs" }, ...trail.map(({ title, href: itemHref }) => ({ title, href: itemHref }))];
}

export function getDocsPager(navItems: DocsNavItem[], href: string) {
  const flatItems = [{ title: "Documentation", href: "/docs" }, ...flattenNavigation(navItems)];
  const index = flatItems.findIndex((item) => item.href === href);
  if (index === -1) {
    return { previous: null, next: null, parent: null } as const;
  }

  const trail = findTrail(navItems, href) ?? [];

  return {
    previous: flatItems[index - 1] ?? null,
    next: flatItems[index + 1] ?? null,
    parent: trail.length > 1 ? trail[trail.length - 2] : flatItems[0],
  } as const;
}

export async function getDocsPage(slug: string[] | undefined): Promise<DocsPageData | null> {
  const docsRoot = await resolveDocsRoot();
  const relativeSlug = sanitizeRelativePath((slug ?? []).join("/"));
  if (relativeSlug == null) {
    return null;
  }

  const baseRelativePath = relativeSlug || "index";
  const markdownPath = path.join(docsRoot, `${baseRelativePath}.md`);
  try {
    const markdown = await fs.readFile(markdownPath, "utf-8");
    const rewrittenMarkdown = rewriteMarkdownLinks(markdown, `${baseRelativePath}.md`);
    return {
      title: await readMarkdownTitle(markdownPath, toTitleCase(path.basename(baseRelativePath))),
      href: slugToHref(baseRelativePath),
      sourcePath: markdownPath,
      markdown: rewrittenMarkdown,
      headings: extractMarkdownHeadings(rewrittenMarkdown),
    };
  } catch {
    const directoryPath = path.join(docsRoot, relativeSlug);
    try {
      const stats = await fs.stat(directoryPath);
      if (!stats.isDirectory()) {
        return null;
      }

      const indexPath = path.join(directoryPath, "index.md");
      try {
        const markdown = await fs.readFile(indexPath, "utf-8");
        const rewrittenMarkdown = rewriteMarkdownLinks(markdown, path.posix.join(relativeSlug, "index.md"));
        return {
          title: await readMarkdownTitle(indexPath, toTitleCase(path.basename(relativeSlug || "docs"))),
          href: slugToHref(relativeSlug),
          sourcePath: indexPath,
          markdown: rewrittenMarkdown,
          headings: extractMarkdownHeadings(rewrittenMarkdown),
        };
      } catch {
        const navItems = await buildNavigationTree(directoryPath, relativeSlug);
        return buildDirectoryListing(relativeSlug, navItems);
      }
    } catch {
      return null;
    }
  }
}
