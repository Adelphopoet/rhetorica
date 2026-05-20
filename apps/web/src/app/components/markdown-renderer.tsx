"use client";

import DOMPurify from "isomorphic-dompurify";
import mermaid from "mermaid/dist/mermaid.esm.mjs";
import { useEffect, useId, useRef } from "react";
import { Marked } from "marked";

import { cn } from "@rhetorica/ui";

import { extractMarkdownHeadings } from "@/lib/markdown";

interface MarkdownRendererProps {
  text: string;
  className?: string;
}

const MERMAID_BLOCK_PATTERN = /```mermaid[^\n]*\n([\s\S]*?)```/g;

let mermaidInitialized = false;

function injectMermaidPlaceholders(text: string): string {
  return text.replace(MERMAID_BLOCK_PATTERN, (_fullMatch, definition: string) => {
    const encodedDefinition = encodeURIComponent(definition.trim());
    return `\n<div class="docs-mermaid-shell"><div class="mermaid-diagram" data-mermaid-definition="${encodedDefinition}"></div></div>\n`;
  });
}

function renderMarkdown(text: string): string {
  try {
    const headings = extractMarkdownHeadings(text);
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

          return `<h${depth} id="${headingId}" class="docs-heading docs-heading-${depth}"><a href="#${headingId}" class="docs-heading-anchor" aria-label="Link to ${escapeHtml(textValue)}">${this.parser.parseInline(tokens)}</a></h${depth}>`;
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

    const rendered = markdown.parse(injectMermaidPlaceholders(text));
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

function ensureMermaidInitialized() {
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      darkMode: true,
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
      themeVariables: {
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
    mermaidInitialized = true;
  }
}

export function MarkdownRenderer({ text, className }: MarkdownRendererProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rendererId = useId();
  const mermaidIdPrefix = rendererId.replaceAll(":", "_");
  const renderedHtml = renderMarkdown(text);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let cancelled = false;

    const renderPlaceholders = async () => {
      ensureMermaidInitialized();
      const placeholders = Array.from(
        container.querySelectorAll<HTMLElement>("[data-mermaid-definition]:not([data-mermaid-rendered])")
      );

      for (const [index, placeholder] of placeholders.entries()) {
        const encodedDefinition = placeholder.dataset.mermaidDefinition;
        if (!encodedDefinition || cancelled) {
          continue;
        }

        placeholder.dataset.mermaidRendered = "pending";
        const definition = decodeURIComponent(encodedDefinition);
        try {
          const renderResult = await mermaid.render(`${mermaidIdPrefix}-${index}`, definition);
          if (cancelled) {
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
          placeholder.innerHTML = DOMPurify.sanitize(
            `<pre><code class="language-mermaid">${escapeHtml(definition)}</code></pre>`,
            { USE_PROFILES: { html: true } }
          );
          placeholder.dataset.mermaidRendered = "error";
        }
      }
    };

    void renderPlaceholders();
    const frameId = window.requestAnimationFrame(() => void renderPlaceholders());
    const timeoutId = window.setTimeout(() => void renderPlaceholders(), 180);

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timeoutId);
    };
  }, [mermaidIdPrefix, renderedHtml]);

  return (
    <div
      ref={containerRef}
      className={cn("chat-markdown docs-markdown text-sm text-[#edf3ff]", className)}
      dangerouslySetInnerHTML={{ __html: renderedHtml }}
    />
  );
}
