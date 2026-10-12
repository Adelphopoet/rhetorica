"use client";

import { useCallback, useRef, useState, type ReactNode } from "react";
import { Button, Dialog } from "@rhetorica/ui";
import { ArrowUpRight, Clock3, Edit, Trash2 } from "lucide-react";

export function ResourceCard({ title, description, icon, status, controls, children, onEdit, onDelete, deleteLabel = "Удалить", onSync, syncPending }: {
  title: string;
  description?: string | null;
  icon: ReactNode;
  status: ReactNode;
  controls?: ReactNode;
  children: ReactNode;
  onEdit: () => void;
  onDelete: () => void;
  deleteLabel?: string;
  onSync?: () => void;
  syncPending?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const changeOpen = useCallback((next: boolean) => {
    setOpen(next);
    if (!next) trigger.current?.focus();
  }, []);

  return (
    <article className="min-w-0 rounded-lg border border-border bg-surface transition hover:border-border-strong">
      <button ref={trigger} type="button" onClick={() => setOpen(true)} aria-label={`Просмотр: ${title}`} aria-haspopup="dialog"
        className="group flex w-full min-w-0 items-center gap-2 rounded-t-lg px-3 pb-1.5 pt-3 text-left outline-none hover:bg-elevated/50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-elevated text-secondary [&>svg]:h-3.5 [&>svg]:w-3.5">{icon}</span>
        <span title={title} className="min-w-0 flex-1 truncate text-sm font-medium text-primary">{title}</span>
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted transition group-hover:text-accent" />
      </button>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-1 px-3 pb-2">
        <div className="flex flex-wrap items-center gap-1">{status}</div>
        <div className="ml-auto flex items-center gap-0.5">
          {controls}
          {onSync && <Button type="button" size="sm" variant="ghost" aria-label="Синхронизировать" title="Синхронизировать" className="h-8 w-8 px-0" onClick={onSync} disabled={syncPending}><Clock3 className="h-3.5 w-3.5" /></Button>}
          <Button type="button" size="sm" variant="ghost" aria-label="Редактировать" title="Редактировать" className="h-8 w-8 px-0" onClick={onEdit}><Edit className="h-3.5 w-3.5" /></Button>
          <Button type="button" size="sm" variant="ghost" aria-label={deleteLabel} title={deleteLabel} className="h-8 w-8 px-0 hover:bg-danger/10 hover:text-danger" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>
        </div>
      </div>
      <Dialog open={open} onOpenChange={changeOpen} title={title} className="max-h-[85dvh] max-w-xl overflow-y-auto rounded-xl bg-surface bg-none p-5" contentClassName="[overflow-wrap:anywhere]">
        <div className="mb-3 flex flex-wrap gap-1.5">{status}</div>
        {description && <p className="mb-4 whitespace-pre-wrap text-sm leading-6 text-secondary [overflow-wrap:anywhere]">{description}</p>}
        <div className="min-w-0 text-sm [overflow-wrap:anywhere]">{children}</div>
      </Dialog>
    </article>
  );
}
