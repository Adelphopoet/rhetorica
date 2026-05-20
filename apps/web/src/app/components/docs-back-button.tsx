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
      className="inline-flex items-center gap-2 rounded-2xl border border-[#22324d] bg-[#0b1525] px-3 py-2 text-sm font-medium text-[#dbe8ff] transition hover:border-[#3f659a] hover:bg-[#10203a] hover:text-white"
    >
      <ArrowLeft className="h-4 w-4" />
      Back
    </button>
  );
}
