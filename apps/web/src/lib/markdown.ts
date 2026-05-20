export interface MarkdownHeading {
  id: string;
  level: number;
  text: string;
}

function stripInlineMarkdown(value: string) {
  return value
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[`*_~]/g, "")
    .replace(/<[^>]+>/g, "")
    .trim();
}

export function slugifyMarkdownHeading(value: string) {
  return stripInlineMarkdown(value)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\u0400-\u04ff\s-]/g, "")
    .trim()
    .replace(/[\s_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function extractMarkdownHeadings(markdown: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = [];
  const slugCounts = new Map<string, number>();
  let insideFence = false;

  for (const rawLine of markdown.split("\n")) {
    const trimmed = rawLine.trim();
    if (trimmed.startsWith("```")) {
      insideFence = !insideFence;
      continue;
    }
    if (insideFence) {
      continue;
    }

    const match = rawLine.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (!match) {
      continue;
    }

    const text = stripInlineMarkdown(match[2]);
    if (!text) {
      continue;
    }

    const baseId = slugifyMarkdownHeading(text) || "section";
    const duplicateCount = slugCounts.get(baseId) ?? 0;
    slugCounts.set(baseId, duplicateCount + 1);

    headings.push({
      id: duplicateCount === 0 ? baseId : `${baseId}-${duplicateCount + 1}`,
      level: match[1].length,
      text,
    });
  }

  return headings;
}
