"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { cn } from "@rhetorica/ui";

import type { MarkdownHeading } from "@/lib/markdown";

interface DocsTableOfContentsProps {
  headings: MarkdownHeading[];
}

export function DocsTableOfContents({ headings }: DocsTableOfContentsProps) {
  const [activeId, setActiveId] = useState<string | null>(headings[0]?.id ?? null);

  useEffect(() => {
    const elements = headings
      .map((heading) => document.getElementById(heading.id))
      .filter((element): element is HTMLElement => element instanceof HTMLElement);

    if (elements.length === 0) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const visibleEntry = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];

        if (visibleEntry?.target.id) {
          setActiveId(visibleEntry.target.id);
        }
      },
      {
        rootMargin: "-18% 0px -64% 0px",
        threshold: [0.1, 0.4, 0.75],
      }
    );

    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [headings]);

  if (headings.length < 2) {
    return null;
  }

  return (
    <aside className="hidden xl:block">
      <div className="sticky top-4 rounded-[28px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-page)/0.9))] p-4 shadow-[0_24px_60px_rgb(var(--color-shadow)/calc(0.28*var(--shadow-opacity-scale)))]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted">На этой странице</p>
        <nav className="mt-4 max-h-[calc(100vh-140px)] overflow-y-auto pr-1">
          <ul className="space-y-1.5">
            {headings
              .filter((heading) => heading.level <= 3)
              .map((heading) => (
                <li key={heading.id}>
                  <Link
                    href={`#${heading.id}`}
                    className={cn(
                      "flex rounded-2xl px-3 py-2 text-sm leading-5 transition",
                      heading.id === activeId
                        ? "bg-elevated text-primary"
                        : "text-secondary hover:bg-surface hover:text-primary"
                    )}
                    style={{ paddingLeft: `${0.75 + (heading.level - 1) * 0.7}rem` }}
                  >
                    {heading.text}
                  </Link>
                </li>
              ))}
          </ul>
        </nav>
      </div>
    </aside>
  );
}
