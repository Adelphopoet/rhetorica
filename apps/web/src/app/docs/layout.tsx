import { ThemeSelector } from "@/features/theme/ThemeSelector";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { DocsSidebar } from "@/app/components/docs-sidebar";
import { getDocsNavigation } from "@/lib/docs";

export default async function DocsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const navItems = await getDocsNavigation();

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,rgb(var(--color-accent)/0.14),transparent_32%),rgb(var(--color-page))] text-primary">
      <div className="mx-auto flex min-h-screen max-w-[1760px] flex-col gap-6 px-3 py-3 lg:h-screen lg:overflow-hidden lg:px-4">
        <div className="flex items-center justify-between rounded-[26px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-page)/0.9),rgb(var(--color-page)/0.82))] px-4 py-3 shadow-[0_18px_50px_rgb(var(--color-shadow)/calc(0.24*var(--shadow-opacity-scale)))]">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted">Rhetorica</p>
            <h1 className="mt-1 text-lg font-semibold tracking-[-0.03em] text-primary">Документация</h1>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
          <ThemeSelector />
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-2xl border border-border-strong bg-surface px-3 py-2 text-sm font-medium text-primary transition hover:border-accent-border hover:bg-elevated hover:text-primary"
          >
            <ArrowLeft className="h-4 w-4" />
            Рабочее пространство
          </Link>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-hidden rounded-[30px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.96),rgb(var(--color-surface)/0.88))] p-5 shadow-[0_24px_60px_rgb(var(--color-shadow)/calc(0.28*var(--shadow-opacity-scale)))]">
            <div className="flex h-full min-h-0 flex-col">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted">Структурированная документация</p>
                <p className="mt-3 text-sm leading-6 text-secondary">
                  Markdown из репозитория, аккуратный Mermaid, закреплённая навигация и быстрый переход к разделам.
                </p>
              </div>

              <div className="mt-6 border-t border-border pt-5">
                <Link
                  href="/docs"
                  className="mb-4 flex rounded-2xl border border-border-strong bg-surface px-3 py-2 text-sm text-primary transition hover:border-accent-border hover:bg-elevated"
                >
                  Указатель документации
                </Link>
              </div>

              <DocsSidebar items={navItems} />
            </div>
          </aside>

          <main className="min-h-0 min-w-0 overflow-y-auto pr-0.5">{children}</main>
        </div>
      </div>
    </div>
  );
}
