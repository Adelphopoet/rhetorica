"use client";

import * as React from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";

import { cn } from "../../lib/utils";

type SheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  side?: "left" | "right";
  className?: string;
};

export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  side = "right",
  className,
}: SheetProps) {
  const prefersReducedMotion = useReducedMotion();

  React.useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onOpenChange(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onOpenChange]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-[95] bg-[#02050c]/76 backdrop-blur-md"
          initial={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          onClick={() => onOpenChange(false)}
        >
          <motion.aside
            role="dialog"
            aria-modal="true"
            className={cn(
              "absolute inset-y-0 flex w-full max-w-[720px] flex-col border-[#1d2940] bg-[linear-gradient(180deg,rgba(12,18,31,0.99),rgba(8,12,22,0.99))] text-[#edf3ff] shadow-[0_30px_90px_rgba(0,0,0,0.48)]",
              side === "right" ? "right-0 border-l" : "left-0 border-r",
              className
            )}
            initial={
              prefersReducedMotion
                ? { opacity: 1 }
                : { opacity: 0, x: side === "right" ? 48 : -48 }
            }
            animate={{ opacity: 1, x: 0 }}
            exit={
              prefersReducedMotion
                ? { opacity: 1 }
                : { opacity: 0, x: side === "right" ? 48 : -48 }
            }
            transition={{ duration: 0.2, ease: "easeOut" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-[#182438] px-5 py-5">
              <div className="min-w-0">
                {title ? <h3 className="text-xl font-semibold text-[#eef4ff]">{title}</h3> : null}
                {description ? (
                  <p className="mt-1 text-sm leading-6 text-[#8ea3c5]">{description}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                aria-label="Close panel"
                className="rounded-full border border-[#23324e] p-2 text-[#a6b8d6] transition hover:border-[#36537a] hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
          </motion.aside>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
