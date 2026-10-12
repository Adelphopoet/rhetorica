"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

export function WorkflowProgress({ label, startedAt }: { label: string; startedAt?: number }) {
  const [started] = useState(() => startedAt ?? Date.now());
  const [seconds, setSeconds] = useState(() => Math.floor((Date.now() - started) / 1000));
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [started]);
  return <div role="status" aria-live="polite" className="flex items-center gap-3 rounded-xl border border-cyan-border/60 bg-cyan-subtle/30 p-4 text-sm text-cyan"><LoaderCircle aria-hidden="true" className="shrink-0 animate-spin" size={20} /><div><p>{label}…</p><p aria-live="off" className="mt-1 text-xs text-muted">{seconds >= 60 ? `${Math.floor(seconds / 60)} мин ${seconds % 60} с` : `${seconds} с`} · Можно продолжать редактирование. Предложение появится здесь.</p></div></div>;
}
