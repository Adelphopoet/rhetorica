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
  keepMounted?: boolean;
};

export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  side = "right",
  className,
  keepMounted = false,
}: SheetProps) {
  const prefersReducedMotion = useReducedMotion();

  // Track where the press started so the backdrop only closes the sheet for a
  // genuine click on it. A `click` fires on the common ancestor of the pointer
  // press and release points, so a press inside the panel followed by a release
  // over the backdrop would otherwise close the sheet too.
  const pointerDownOnBackdrop = React.useRef(false);

  const handleBackdropPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    pointerDownOnBackdrop.current = event.target === event.currentTarget;
  };

  const handleBackdropPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const closesSheet = pointerDownOnBackdrop.current && event.target === event.currentTarget;
    pointerDownOnBackdrop.current = false;
    if (closesSheet) {
      onOpenChange(false);
    }
  };

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

  const header = (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-5">
      <div className="min-w-0">
        {title ? <h3 className="text-xl font-semibold text-primary">{title}</h3> : null}
        {description ? (
          <p className="mt-1 text-sm leading-6 text-secondary">{description}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onOpenChange(false)}
        aria-label="Закрыть панель"
        className="rounded-full border border-border-strong p-2 text-secondary transition hover:border-border-strong hover:text-primary"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );

  if (keepMounted) {
    return (
      <div
        className={cn(
          "fixed inset-0 z-[95]",
          open ? "bg-overlay/[0.76] backdrop-blur-md" : "hidden"
        )}
        onPointerDown={handleBackdropPointerDown}
        onPointerUp={handleBackdropPointerUp}
      >
        <aside
          role="dialog"
          aria-modal="true"
          aria-hidden={!open}
          className={cn(
            "absolute inset-y-0 flex w-full max-w-[720px] flex-col border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.99),rgb(var(--color-surface)/0.99))] text-primary shadow-[0_30px_90px_rgb(var(--color-shadow)/calc(0.48*var(--shadow-opacity-scale)))]",
            side === "right" ? "right-0 border-l" : "left-0 border-r",
            className
          )}
          onClick={(event) => event.stopPropagation()}
        >
          {header}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
        </aside>
      </div>
    );
  }

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-[95] bg-overlay/[0.76] backdrop-blur-md"
          initial={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          onPointerDown={handleBackdropPointerDown}
          onPointerUp={handleBackdropPointerUp}
        >
          <motion.aside
            role="dialog"
            aria-modal="true"
            className={cn(
              "absolute inset-y-0 flex w-full max-w-[720px] flex-col border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.99),rgb(var(--color-surface)/0.99))] text-primary shadow-[0_30px_90px_rgb(var(--color-shadow)/calc(0.48*var(--shadow-opacity-scale)))]",
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
{header}
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
          </motion.aside>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
