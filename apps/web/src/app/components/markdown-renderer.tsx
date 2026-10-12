"use client";

import DOMPurify from "isomorphic-dompurify";
import mermaid from "mermaid/dist/mermaid.esm.mjs";
import { useEffect, useId, useRef } from "react";
import { Marked } from "marked";

import { cn } from "@rhetorica/ui";

import { extractMarkdownHeadings } from "@/lib/markdown";
import { useTheme } from "@/features/theme/ThemeProvider";

interface MarkdownRendererProps {
  text: string;
  className?: string;
}

const MERMAID_BLOCK_PATTERN = /```mermaid[^\n]*\n([\s\S]*?)```/g;
const VISUALIZATION_JSON_BLOCK_PATTERN = /```(?:json)?[^\n]*\n([\s\S]*?)```/gi;

// Mermaid owns global configuration. Serialize initialization and render so a
// concurrent diagram cannot change the theme of a render already in progress.
let mermaidRenderQueue: Promise<unknown> = Promise.resolve();

function injectMermaidPlaceholders(text: string): string {
  return text.replace(MERMAID_BLOCK_PATTERN, (_fullMatch, definition: string) => {
    const encodedDefinition = encodeURIComponent(definition.trim());
    return `\n<div class="docs-mermaid-shell"><div class="mermaid-diagram" data-mermaid-definition="${encodedDefinition}"></div></div>\n`;
  });
}

function removeVisualizationJsonBlocks(text: string): string {
  return text.replace(VISUALIZATION_JSON_BLOCK_PATTERN, (fullMatch, body: string) =>
    isVisualizationProtocolJson(body) ? "" : fullMatch
  );
}

function isVisualizationProtocolJson(value: string): boolean {
  try {
    const payload = JSON.parse(value.trim()) as unknown;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return false;
    }
    const record = payload as Record<string, unknown>;
    if (record.visualization && typeof record.visualization === "object" && !Array.isArray(record.visualization)) {
      return true;
    }
    return (
      (record.version === "v1" || record.version === "v2") &&
      typeof record.kind === "string" &&
      Boolean(record.data_source && typeof record.data_source === "object" && !Array.isArray(record.data_source)) &&
      Boolean(record.encoding && typeof record.encoding === "object" && !Array.isArray(record.encoding))
    );
  } catch {
    return false;
  }
}

function renderMarkdown(text: string): string {
  try {
    const displayText = removeVisualizationJsonBlocks(text);
    const headings = extractMarkdownHeadings(displayText);
    const headingIds = new Map(headings.map((heading) => [heading.text, heading.id]));
    const localHeadingCounts = new Map<string, number>();
    const markdown = new Marked({
      gfm: true,
      breaks: true,
      renderer: {
        link({ href, title, tokens }) {
          const label = this.parser.parseInline(tokens);
          const url = href ?? "";
          const isExternal = /^https?:\/\//i.test(url);
          const titleAttribute = title ? ` title="${title}"` : "";
          const externalAttributes = isExternal ? ' target="_blank" rel="noopener noreferrer"' : "";

          return `<a href="${url}"${titleAttribute}${externalAttributes}>${label}</a>`;
        },
        heading({ tokens, depth }) {
          const textValue = this.parser.parseInline(tokens).replace(/<[^>]+>/g, "").trim();
          const occurrence = localHeadingCounts.get(textValue) ?? 0;
          localHeadingCounts.set(textValue, occurrence + 1);
          const matchingIds = headings.filter((heading) => heading.text === textValue).map((heading) => heading.id);
          const headingId = matchingIds[occurrence] ?? headingIds.get(textValue) ?? "section";

          return `<h${depth} id="${headingId}" class="docs-heading docs-heading-${depth}"><a href="#${headingId}" class="docs-heading-anchor" aria-label="Ссылка на ${escapeHtml(textValue)}">${this.parser.parseInline(tokens)}</a></h${depth}>`;
        },
      },
      hooks: {
        postprocess(html) {
          return DOMPurify.sanitize(html, {
            USE_PROFILES: { html: true },
            ADD_ATTR: ["target", "rel", "data-mermaid-definition", "data-mermaid-rendered", "id", "aria-label", "class"],
          });
        },
      },
    });

    const rendered = markdown.parse(injectMermaidPlaceholders(displayText));
    return typeof rendered === "string" ? rendered : "";
  } catch {
    return DOMPurify.sanitize(text, { USE_PROFILES: { html: true } });
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function initializeMermaid(theme: "light" | "dark") {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      darkMode: theme === "dark",
      deterministicIds: true,
      look: "classic",
      htmlLabels: false,
      flowchart: {
        useMaxWidth: true,
        curve: "basis",
      },
      sequence: {
        useMaxWidth: true,
      },
      themeVariables: theme === "light" ? {
        fontFamily: "var(--font-sans), Avenir Next, Segoe UI, sans-serif",
        primaryColor: "#e8f0fc", primaryBorderColor: "#4776ac", primaryTextColor: "#17243b",
        lineColor: "#526f94", secondaryColor: "#f2f6fc", secondaryBorderColor: "#758ead",
        tertiaryColor: "#ffffff", tertiaryTextColor: "#263954", background: "#ffffff",
        mainBkg: "#e8f0fc", secondBkg: "#f2f6fc", tertiaryBorderColor: "#879bb8",
        clusterBkg: "#f2f6fc", clusterBorder: "#b7c9de", edgeLabelBackground: "#ffffff",
      } : {
        fontFamily: "var(--font-sans), Avenir Next, Segoe UI, sans-serif",
        primaryColor: "#101b2d",
        primaryBorderColor: "#5f8fcb",
        primaryTextColor: "#eef4ff",
        lineColor: "#6f97c9",
        secondaryColor: "#0d1728",
        secondaryBorderColor: "#4f78ad",
        tertiaryColor: "#0a1322",
        tertiaryTextColor: "#dce7ff",
        background: "#08111d",
        mainBkg: "#101b2d",
        secondBkg: "#0d1728",
        tertiaryBorderColor: "#34527f",
        clusterBkg: "rgba(13, 23, 40, 0.92)",
        clusterBorder: "#223b5d",
        edgeLabelBackground: "#08111d",
      },
    });
}

export function MarkdownRenderer({ text, className }: MarkdownRendererProps) {
  const { resolvedTheme } = useTheme();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const generation = useRef(0);
  const rendererId = useId();
  const mermaidIdPrefix = rendererId.replaceAll(":", "_");
  const renderedHtml = renderMarkdown(text);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let cancelled = false;
    const renderGeneration = ++generation.current;

    const renderPlaceholders = async () => {
      const placeholders = Array.from(
        container.querySelectorAll<HTMLElement>("[data-mermaid-definition]")
      );

      for (const [index, placeholder] of placeholders.entries()) {
        const encodedDefinition = placeholder.dataset.mermaidDefinition;
        if (!encodedDefinition || cancelled) {
          continue;
        }

        placeholder.dataset.mermaidRendered = "pending";
        placeholder.replaceChildren();
        let definition = encodedDefinition;
        try {
          definition = decodeURIComponent(encodedDefinition);
          const pending = mermaidRenderQueue.then(() => {
            if (cancelled) return null;
            initializeMermaid(resolvedTheme);
            return mermaid.render(`${mermaidIdPrefix}-${renderGeneration}-${index}`, definition);
          });
          // Always release the queue after a failed definition.
          mermaidRenderQueue = pending.catch(() => undefined);
          const renderResult = await pending;
          if (cancelled || !renderResult) {
            return;
          }
          placeholder.innerHTML = renderResult.svg;
          const svg = placeholder.querySelector("svg");
          if (svg) {
            svg.removeAttribute("height");
            svg.style.height = "auto";
            svg.style.maxWidth = "none";
            svg.style.display = "block";
          }
          renderResult.bindFunctions?.(placeholder);
          placeholder.dataset.mermaidRendered = "true";
        } catch {
          if (cancelled) return;
          placeholder.innerHTML = DOMPurify.sanitize(
            `<pre><code class="language-mermaid">${escapeHtml(definition)}</code></pre>`,
            { USE_PROFILES: { html: true } }
          );
          placeholder.dataset.mermaidRendered = "error";
        }
      }
    };

    void renderPlaceholders();

    return () => {
      cancelled = true;
    };
  }, [mermaidIdPrefix, renderedHtml, resolvedTheme]);

  return (
    <div
      ref={containerRef}
      className={cn("chat-markdown docs-markdown text-sm text-primary", className)}
      dangerouslySetInnerHTML={{ __html: renderedHtml }}
    />
  );
}
