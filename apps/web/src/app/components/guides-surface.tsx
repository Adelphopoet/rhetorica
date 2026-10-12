"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Button, ScrollArea } from "@rhetorica/ui";
import { Diamond, ExternalLink, PanelRightClose } from "lucide-react";

const DESKTOP_BREAKPOINT_PX = 1280;
const DEFAULT_PANEL_WIDTH_PX = 520;
const MIN_PANEL_WIDTH_PX = 380;
const MIN_MAIN_WORKSPACE_WIDTH_PX = 520;

const GUIDE_LINKS = [
  {
    id: "openmetadata-metric-description",
    title: "Заполнение метрик в OpenMetadata",
    href: "https://example.com/docs/metadata",
  },
] as const;

function getMaxPanelWidth(layoutWidth: number): number {
  return Math.max(MIN_PANEL_WIDTH_PX, Math.floor(layoutWidth - MIN_MAIN_WORKSPACE_WIDTH_PX));
}

function clampPanelWidth(width: number, layoutWidth: number): number {
  const maxWidth = getMaxPanelWidth(layoutWidth);
  return Math.min(Math.max(Math.round(width), MIN_PANEL_WIDTH_PX), maxWidth);
}

function resolveDesktopLayoutBounds(panel: HTMLElement | null): { width: number } | null {
  if (typeof window === "undefined") {
    return null;
  }

  const parentRect = panel?.parentElement?.getBoundingClientRect();
  if (parentRect && Number.isFinite(parentRect.width) && parentRect.width > 0) {
    return { width: parentRect.width };
  }

  return { width: window.innerWidth };
}

interface GuidesSurfaceProps {
  onClose: () => void;
}

export function GuidesSurface({ onClose }: GuidesSurfaceProps) {
  const shouldReduceMotion = useReducedMotion();
  const [desktopWidth, setDesktopWidth] = useState(DEFAULT_PANEL_WIDTH_PX);
  const panelRef = useRef<HTMLElement | null>(null);

  const syncWidthToLayout = useCallback(() => {
    if (typeof window === "undefined" || window.innerWidth < DESKTOP_BREAKPOINT_PX) {
      return;
    }

    const layoutBounds = resolveDesktopLayoutBounds(panelRef.current);
    if (!layoutBounds) {
      return;
    }

    setDesktopWidth((current) => clampPanelWidth(current, layoutBounds.width));
  }, []);

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      syncWidthToLayout();
    });

    function handleResize() {
      syncWidthToLayout();
    }

    window.addEventListener("resize", handleResize);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", handleResize);
    };
  }, [syncWidthToLayout]);

  const panelWidthStyle = {
    "--guides-surface-width": `${desktopWidth}px`,
  } as CSSProperties;

  return (
    <>
      <button
        type="button"
        aria-label="Close guides panel"
        onClick={onClose}
        className="fixed inset-0 z-30 bg-overlay/[0.72] backdrop-blur-sm transition xl:hidden"
      />

      <motion.aside
        ref={panelRef}
        initial={shouldReduceMotion ? { opacity: 0 } : { x: 32, opacity: 0 }}
        animate={shouldReduceMotion ? { opacity: 1 } : { x: 0, opacity: 1 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
        style={panelWidthStyle}
        data-testid="guides-surface"
        data-panel-state="open"
        className="fixed inset-y-3 right-3 z-40 flex w-full max-w-[min(100vw-24px,560px)] flex-col rounded-[28px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-page)/0.98),rgb(var(--color-surface)/0.98))] shadow-[-24px_0_80px_rgb(var(--color-shadow)/calc(0.45*var(--shadow-opacity-scale)))] duration-200 ease-out xl:relative xl:inset-auto xl:right-auto xl:z-0 xl:h-full xl:max-w-none xl:shrink-0 xl:overflow-hidden xl:rounded-none xl:rounded-r-[32px] xl:border-y-0 xl:border-r-0 xl:shadow-none xl:w-[var(--guides-surface-width)] xl:min-w-[var(--guides-surface-width)]"
      >
        <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 sm:px-5 xl:px-6">
          <h2 className="text-base font-semibold tracking-[-0.03em] text-primary">
            Полезные инструкции
          </h2>

          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="gap-2"
          >
            <PanelRightClose className="h-4 w-4" />
            <span className="hidden xl:inline">Скрыть</span>
          </Button>
        </div>

        <ScrollArea className="flex-1">
          <div className="px-4 py-4 sm:px-5 xl:px-6">
            <ul className="space-y-1">
              {GUIDE_LINKS.map((guide) => (
                <li key={guide.id}>
                  <a
                    href={guide.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-2 rounded-[22px] py-2.5 text-sm font-medium text-secondary transition hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    <Diamond className="h-2.5 w-2.5 shrink-0 text-border-strong transition-colors group-hover:text-secondary" />
                    <span className="min-w-0 flex-1">{guide.title}</span>
                    <ExternalLink className="h-3.5 w-3.5 shrink-0 text-muted transition-colors group-hover:text-secondary" />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </ScrollArea>
      </motion.aside>
    </>
  );
}
