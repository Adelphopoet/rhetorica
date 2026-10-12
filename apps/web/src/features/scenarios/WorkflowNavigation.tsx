import Link from "next/link";
import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { ArrowLeft, ChevronRight, Workflow } from "lucide-react";

export function WorkflowNavigation({ current }: { current: string }) {
  return <nav aria-label="Навигация сценария" className="flex flex-wrap items-center gap-3 text-sm">
    <Link href="/" className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-border-strong bg-elevated/70 px-3 text-primary transition-colors hover:border-border-strong hover:bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan">
      <ArrowLeft size={16} aria-hidden="true" /> На главную
    </Link>
    <ChevronRight size={14} aria-hidden="true" className="text-muted" />
    <span aria-current="page" className="flex items-center gap-2 text-muted"><Workflow size={15} aria-hidden="true" />{current}</span>
    <div className="ml-auto"><ThemeSelector /></div>
  </nav>;
}
