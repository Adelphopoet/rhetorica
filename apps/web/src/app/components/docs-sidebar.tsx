"use client";

import { useDeferredValue, useMemo, useState } from "react";
import { ChevronRight, FileText, FolderTree, Search } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@rhetorica/ui";

import type { DocsNavItem } from "@/lib/docs";

interface DocsSidebarProps {
  items: DocsNavItem[];
}

function isActivePath(pathname: string, href: string) {
  return pathname === href || (href !== "/docs" && pathname.startsWith(`${href}/`));
}

function collectExpandedHrefs(items: DocsNavItem[], pathname: string, expanded = new Set<string>()) {
  for (const item of items) {
    if (item.children.length === 0) {
      continue;
    }
    if (isActivePath(pathname, item.href)) {
      expanded.add(item.href);
    }
    collectExpandedHrefs(item.children, pathname, expanded);
  }
  return expanded;
}

function filterItems(items: DocsNavItem[], query: string): DocsNavItem[] {
  if (!query) {
    return items;
  }

  return items.flatMap((item) => {
    const filteredChildren = filterItems(item.children, query);
    const matches = item.title.toLowerCase().includes(query);
    if (!matches && filteredChildren.length === 0) {
      return [];
    }

    return [
      {
        ...item,
        children: filteredChildren,
      },
    ];
  });
}

function countPages(items: DocsNavItem[]): number {
  return items.reduce((total, item) => total + 1 + countPages(item.children), 0);
}

interface SidebarBranchProps {
  items: DocsNavItem[];
  depth: number;
  expanded: Set<string>;
  onToggle: (href: string) => void;
  pathname: string;
  searching: boolean;
}

function SidebarBranch({ items, depth, expanded, onToggle, pathname, searching }: SidebarBranchProps) {
  return (
    <ul className={cn("space-y-1.5", depth > 0 ? "mt-2 border-l border-[#15243a] pl-3" : "")}>
      {items.map((item) => {
        const isActive = pathname === item.href;
        const isBranchActive = isActivePath(pathname, item.href);
        const hasChildren = item.children.length > 0;
        const isExpanded = searching || expanded.has(item.href);

        return (
          <li key={item.href}>
            <div className="flex items-center gap-2">
              {hasChildren ? (
                <button
                  type="button"
                  onClick={() => onToggle(item.href)}
                  aria-label={isExpanded ? `Collapse ${item.title}` : `Expand ${item.title}`}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-transparent text-[#7690b6] transition hover:border-[#22324d] hover:bg-[#0d1728] hover:text-white"
                >
                  <ChevronRight
                    className={cn("h-4 w-4 transition-transform", isExpanded ? "rotate-90" : "")}
                  />
                </button>
              ) : (
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center text-[#415572]">
                  <FileText className="h-3.5 w-3.5" />
                </span>
              )}

              <Link
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "group flex min-w-0 flex-1 items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition",
                  isActive
                    ? "border-[#4f7ebb] bg-[#112039] text-[#eef4ff]"
                    : isBranchActive
                      ? "border-[#22324d] bg-[#0d1728] text-[#dce8ff]"
                      : "border-transparent text-[#aab9d2] hover:border-[#22324d] hover:bg-[#0d1728] hover:text-white"
                )}
              >
                {hasChildren ? (
                  <FolderTree className="h-3.5 w-3.5 shrink-0 text-[#81a7dd]" />
                ) : (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#4f7ebb]" />
                )}
                <span className="truncate">{item.title}</span>
              </Link>
            </div>

            {hasChildren && isExpanded ? (
              <SidebarBranch
                items={item.children}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
                pathname={pathname}
                searching={searching}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function DocsSidebar({ items }: DocsSidebarProps) {
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const normalizedQuery = deferredQuery.trim().toLowerCase();
  const filteredItems = useMemo(() => filterItems(items, normalizedQuery), [items, normalizedQuery]);
  const [expanded, setExpanded] = useState<Set<string>>(() => collectExpandedHrefs(items, pathname));
  const visibleExpanded = useMemo(() => {
    const next = new Set(expanded);
    collectExpandedHrefs(items, pathname).forEach((href) => next.add(href));
    return next;
  }, [expanded, items, pathname]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="rounded-[26px] border border-[#18263b] bg-[linear-gradient(180deg,rgba(11,18,31,0.95),rgba(8,13,24,0.92))] p-3">
        <label className="flex items-center gap-2 rounded-2xl border border-[#1d2d45] bg-[#09111d] px-3 py-2 text-sm text-[#8da4c7] focus-within:border-[#4773ac] focus-within:text-[#dce9ff]">
          <Search className="h-4 w-4 shrink-0" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter docs"
            className="w-full bg-transparent text-sm text-[#eef4ff] outline-none placeholder:text-[#60789f]"
          />
        </label>
      </div>

      <div className="mt-4 flex items-center justify-between px-1">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#6e87aa]">Sections</p>
        <p className="rounded-full border border-[#1d2c44] px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#8fa6c8]">
          {countPages(filteredItems)} items
        </p>
      </div>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1">
        {filteredItems.length > 0 ? (
          <SidebarBranch
            items={filteredItems}
            depth={0}
            expanded={visibleExpanded}
            onToggle={(href) =>
              setExpanded((current) => {
                const next = new Set(current);
                if (next.has(href)) {
                  next.delete(href);
                } else {
                  next.add(href);
                }
                return next;
              })
            }
            pathname={pathname}
            searching={normalizedQuery.length > 0}
          />
        ) : (
          <div className="rounded-[24px] border border-dashed border-[#24354f] bg-[#0a111d] px-4 py-6 text-sm text-[#8da4c7]">
            Nothing found. Tighten naming in docs or type a better query.
          </div>
        )}
      </div>
    </div>
  );
}
