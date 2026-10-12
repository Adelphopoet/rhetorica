"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";

interface DocsBackButtonProps {
  fallbackHref: string;
}

export function DocsBackButton({ fallbackHref }: DocsBackButtonProps) {
  const router = useRouter();

  return (
    <button
      type="button"
      onClick={() => {
        if (window.history.length > 1) {
          router.back();
          return;
        }
        router.push(fallbackHref);
      }}
      className="inline-flex items-center gap-2 rounded-2xl border border-border-strong bg-surface px-3 py-2 text-sm font-medium text-primary transition hover:border-accent-border hover:bg-elevated hover:text-primary"
    >
      <ArrowLeft className="h-4 w-4" />
      Назад
    </button>
  );
}
