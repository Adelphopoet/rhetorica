"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";

import { cn } from "../../lib/utils";

function getFocusableElements(container: HTMLElement | null) {
  if (!container) {
    return [];
  }

  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
  ).filter((element) => !element.hasAttribute("disabled") && !element.getAttribute("aria-hidden"));
}

function useOverlayLifecycle(open: boolean, onOpenChange: (open: boolean) => void) {
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
}

type DialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
  overlayClassName?: string;
  showCloseButton?: boolean;
};

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
  contentClassName,
  overlayClassName,
  showCloseButton = true,
}: DialogProps) {
  const prefersReducedMotion = useReducedMotion();
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  useOverlayLifecycle(open, onOpenChange);

  React.useEffect(() => {
    if (!open) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      const focusable = getFocusableElements(containerRef.current);
      const preferred = containerRef.current?.querySelector<HTMLElement>("[data-autofocus]");
      (preferred ?? focusable[0] ?? containerRef.current)?.focus();
    });

    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const handleKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") {
      return;
    }

    const focusable = getFocusableElements(containerRef.current);
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && active === first) {
      event.preventDefault();
      last?.focus();
      return;
    }

    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    }
  }, []);

  if (!mounted) {
    return null;
  }

  return createPortal(
    <AnimatePresence>
      {open ? (
        <motion.div
          aria-hidden={!open}
          className={cn(
            "fixed inset-0 z-[90] flex items-center justify-center bg-overlay/[0.78] p-4 backdrop-blur-md",
            overlayClassName
          )}
          initial={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={prefersReducedMotion ? { opacity: 1 } : { opacity: 0 }}
          onClick={() => onOpenChange(false)}
        >
          <motion.div
            ref={containerRef}
            role="dialog"
            aria-modal="true"
            tabIndex={-1}
            className={cn(
              "relative w-full max-w-2xl rounded-[28px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.99))] p-6 text-primary shadow-[0_30px_90px_rgb(var(--color-shadow)/calc(0.48*var(--shadow-opacity-scale)))] outline-none",
              className
            )}
            initial={prefersReducedMotion ? { opacity: 1 } : { opacity: 0, y: 18, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={prefersReducedMotion ? { opacity: 1 } : { opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={handleKeyDown}
          >
            {(title || description || showCloseButton) && (
              <div className="mb-5 flex items-start justify-between gap-4">
                <div className={cn("min-w-0", contentClassName)}>
                  {title ? (
                    <h3 className="text-xl font-semibold text-primary">{title}</h3>
                  ) : null}
                  {description ? (
                    <p className="mt-1 text-sm leading-6 text-secondary">{description}</p>
                  ) : null}
                </div>
                {showCloseButton ? (
                  <button
                    type="button"
                    onClick={() => onOpenChange(false)}
                    aria-label="Закрыть диалог"
                    className="rounded-full border border-border-strong p-2 text-secondary transition hover:border-border-strong hover:text-primary"
                  >
                    <X className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            )}
            {children}
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>,
    document.body
  );
}

type ConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: "primary" | "danger";
  onConfirm: () => void;
  isPending?: boolean;
};

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Подтвердить",
  cancelLabel = "Отмена",
  confirmVariant = "danger",
  onConfirm,
  isPending = false,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}>
      <div className="flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="inline-flex h-10 items-center justify-center rounded-xl border border-border-strong bg-surface px-4 text-sm font-medium text-primary transition hover:bg-elevated"
        >
          {cancelLabel}
        </button>
        <button
          type="button"
          data-autofocus
          disabled={isPending}
          onClick={onConfirm}
          className={cn(
            "inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-medium text-on-accent transition disabled:cursor-not-allowed disabled:opacity-70",
            confirmVariant === "danger"
              ? "bg-danger shadow-[0_10px_30px_rgb(var(--color-danger)/0.25)] hover:bg-danger"
              : "bg-accent shadow-[0_10px_30px_rgb(var(--color-accent)/0.25)] hover:bg-accent"
          )}
        >
          {isPending ? "Working..." : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}
