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
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(93,168,255,0.14),transparent_32%),#050a14] text-[#eef4ff]">
      <div className="mx-auto flex min-h-screen max-w-[1760px] flex-col gap-6 px-3 py-3 lg:h-screen lg:overflow-hidden lg:px-4">
        <div className="flex items-center justify-between rounded-[26px] border border-[#162339] bg-[linear-gradient(180deg,rgba(7,11,19,0.9),rgba(6,10,18,0.82))] px-4 py-3 shadow-[0_18px_50px_rgba(0,0,0,0.24)]">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#6e87aa]">Rhetorica Workspace</p>
            <h1 className="mt-1 text-lg font-semibold tracking-[-0.03em] text-white">Docs</h1>
          </div>
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-2xl border border-[#22324d] bg-[#0b1525] px-3 py-2 text-sm font-medium text-[#dbe8ff] transition hover:border-[#3f659a] hover:bg-[#10203a] hover:text-white"
          >
            <ArrowLeft className="h-4 w-4" />
            Workspace
          </Link>
        </div>

        <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-hidden rounded-[30px] border border-[#172338] bg-[linear-gradient(180deg,rgba(8,12,21,0.96),rgba(8,12,21,0.88))] p-5 shadow-[0_24px_60px_rgba(0,0,0,0.28)]">
            <div className="flex h-full min-h-0 flex-col">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#6e87aa]">Structured Docs</p>
                <p className="mt-3 text-sm leading-6 text-[#9bb0d1]">
                  Repo-backed markdown, cleaner Mermaid, sticky navigation, and fast section jumping.
                </p>
              </div>

              <div className="mt-6 border-t border-[#132033] pt-5">
                <Link
                  href="/docs"
                  className="mb-4 flex rounded-2xl border border-[#22324d] bg-[#0c1524] px-3 py-2 text-sm text-[#eef4ff] transition hover:border-[#3b5d8e] hover:bg-[#10203a]"
                >
                  Documentation Index
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
