import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronRight, FileCode2 } from "lucide-react";

import { DocsBackButton } from "@/app/components/docs-back-button";
import { DocsTableOfContents } from "@/app/components/docs-table-of-contents";
import { MarkdownRenderer } from "@/app/components/markdown-renderer";
import { getDocsBreadcrumbs, getDocsNavigation, getDocsPage, getDocsPager } from "@/lib/docs";

interface DocsPageProps {
  params: Promise<{
    slug?: string[];
  }>;
}

export async function generateMetadata({ params }: DocsPageProps): Promise<Metadata> {
  const { slug } = await params;
  const page = await getDocsPage(slug);
  const title = page ? `${page.title} | Rhetorica` : "Документация | Rhetorica";
  return { title };
}

export default async function DocsPage({ params }: DocsPageProps) {
  const { slug } = await params;
  const page = await getDocsPage(slug);

  if (!page) {
    notFound();
  }

  const navItems = await getDocsNavigation();
  const breadcrumbs = getDocsBreadcrumbs(navItems, page.href);
  const pager = getDocsPager(navItems, page.href);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_280px]">
      <article className="rounded-[34px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.94),rgb(var(--color-surface)/0.9))] px-5 py-6 shadow-[0_30px_80px_rgb(var(--color-shadow)/calc(0.3*var(--shadow-opacity-scale)))] sm:px-8 sm:py-8">
        <div className="mb-8 border-b border-border pb-6">
          <div className="flex flex-wrap items-center gap-3">
            <DocsBackButton fallbackHref={pager.parent?.href ?? "/docs"} />
            {pager.parent ? (
              <Link
                href={pager.parent.href}
                className="inline-flex items-center gap-2 rounded-2xl border border-border bg-surface px-3 py-2 text-sm text-secondary transition hover:border-accent-border hover:text-primary"
              >
                Наверх: {pager.parent.title}
              </Link>
            ) : null}
          </div>

          <nav className="mt-5 flex flex-wrap items-center gap-2 text-sm text-secondary">
            {breadcrumbs.map((item, index) => (
              <span key={item.href} className="inline-flex items-center gap-2">
                {index > 0 ? <ChevronRight className="h-4 w-4 text-muted" /> : null}
                {index === breadcrumbs.length - 1 ? (
                  <span className="rounded-full border border-border-strong bg-surface px-3 py-1.5 text-primary">
                    {item.title}
                  </span>
                ) : (
                  <Link href={item.href} className="transition hover:text-primary">
                    {item.title}
                  </Link>
                )}
              </span>
            ))}
          </nav>

          <div className="mt-5 flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-3xl">
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">Источник</p>
              <div className="mt-2 inline-flex max-w-full items-center gap-2 rounded-2xl border border-border bg-surface px-3 py-2 text-xs text-secondary">
                <FileCode2 className="h-4 w-4 shrink-0 text-accent" />
                <span className="break-all font-mono">{page.sourcePath}</span>
              </div>
            </div>

            <div className="rounded-[24px] border border-border bg-surface px-4 py-3 text-sm text-secondary">
              {page.headings.length} разделов
            </div>
          </div>
        </div>

        <MarkdownRenderer text={page.markdown} />

        {(pager.previous || pager.next) ? (
          <div className="mt-10 grid gap-3 border-t border-border pt-6 md:grid-cols-2">
            {pager.previous ? (
              <Link
                href={pager.previous.href}
                className="rounded-[24px] border border-border bg-surface px-4 py-4 transition hover:border-accent-border hover:bg-elevated"
              >
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">Назад</p>
                <p className="mt-2 text-base font-semibold text-primary">{pager.previous.title}</p>
              </Link>
            ) : (
              <div />
            )}
            {pager.next ? (
              <Link
                href={pager.next.href}
                className="rounded-[24px] border border-border bg-surface px-4 py-4 text-left transition hover:border-accent-border hover:bg-elevated"
              >
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">Вперёд</p>
                <p className="mt-2 text-base font-semibold text-primary">{pager.next.title}</p>
              </Link>
            ) : null}
          </div>
        ) : null}
      </article>

      <DocsTableOfContents headings={page.headings} />
    </div>
  );
}
