"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/utils";

type TooltipSide = "top" | "bottom" | "left" | "right";

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: TooltipSide;
  /** Overrides the placement from `sm`/`lg` up, keeping `side` for mobile */
  desktopSide?: TooltipSide;
  className?: string;
}

const arrowClasses: Record<TooltipSide, string> = {
  top: "bottom-[-5px] left-1/2 -translate-x-1/2 border-r border-b rotate-45",
  bottom: "top-[-5px] left-1/2 -translate-x-1/2 border-l border-t rotate-45",
  left: "right-[-5px] top-1/2 -translate-y-1/2 border-b border-r rotate-45",
  right: "left-[-5px] top-1/2 -translate-y-1/2 border-l border-t rotate-45",
};

function TooltipPopover({
  side,
  content,
  className,
  triggerRect,
  gap,
}: {
  side: TooltipSide;
  content: React.ReactNode;
  className?: string;
  triggerRect: DOMRect;
  gap: number;
}) {
  const style: React.CSSProperties =
    side === "top"
      ? { left: triggerRect.left + triggerRect.width / 2, top: triggerRect.top - gap, transform: "translate(-50%, -100%)" }
      : side === "bottom"
        ? { left: triggerRect.left + triggerRect.width / 2, top: triggerRect.bottom + gap, transform: "translate(-50%, 0)" }
        : side === "left"
          ? { left: triggerRect.left - gap, top: triggerRect.top + triggerRect.height / 2, transform: "translate(-100%, -50%)" }
          : { left: triggerRect.right + gap, top: triggerRect.top + triggerRect.height / 2, transform: "translate(0, -50%)" };

  return (
    <div className={cn("pointer-events-none fixed z-[1000]", className)} style={style}>
      <div className="relative px-2.5 py-1.5 text-xs font-medium text-primary bg-hover border border-border-strong rounded-lg shadow-xl whitespace-nowrap animate-in fade-in zoom-in-95 duration-150">
        {content}
        <div className={cn("absolute w-2 h-2 bg-hover border-border-strong", arrowClasses[side])} />
      </div>
    </div>
  );
}

const Tooltip = React.forwardRef<HTMLDivElement, TooltipProps>(
  ({ content, children, side = "top", desktopSide, className }, ref) => {
    const [isVisible, setIsVisible] = React.useState(false);
    const triggerRef = React.useRef<HTMLDivElement | null>(null);
    const [triggerRect, setTriggerRect] = React.useState<DOMRect | null>(null);

    React.useImperativeHandle(ref, () => triggerRef.current as HTMLDivElement);

    React.useLayoutEffect(() => {
      if (!isVisible) {
        return;
      }
      const measure = () => {
        const trigger = triggerRef.current;
        if (trigger) {
          setTriggerRect(trigger.getBoundingClientRect());
        }
      };
      measure();
      window.addEventListener("scroll", measure, true);
      window.addEventListener("resize", measure);
      return () => {
        window.removeEventListener("scroll", measure, true);
        window.removeEventListener("resize", measure);
      };
    }, [isVisible]);

    if (typeof document === "undefined") {
      return <div ref={triggerRef} className="relative inline-block">{children}</div>;
    }

return (
      <>
        <div
          ref={triggerRef}
          className="relative inline-block"
          onMouseEnter={() => setIsVisible(true)}
          onMouseLeave={() => setIsVisible(false)}
        >
          {children}
        </div>
        {isVisible && triggerRect ? (
          desktopSide ? (
            <>
              {createPortal(
                <TooltipPopover side={side} content={content} className={cn("lg:hidden", className)} triggerRect={triggerRect} gap={8} />,
                document.body
              )}
              {createPortal(
                <TooltipPopover side={desktopSide} content={content} className={cn("hidden lg:block", className)} triggerRect={triggerRect} gap={8} />,
                document.body
              )}
            </>
          ) : (
            createPortal(
              <TooltipPopover side={side} content={content} className={className} triggerRect={triggerRect} gap={8} />,
              document.body
            )
          )
        ) : null}
      </>
    );
  }
);
Tooltip.displayName = "Tooltip";

export { Tooltip };
