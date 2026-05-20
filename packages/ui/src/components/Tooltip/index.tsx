"use client";

import * as React from "react";
import { cn } from "../../lib/utils";

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}

const Tooltip = React.forwardRef<HTMLDivElement, TooltipProps>(
  ({ content, children, side = "top", className }, ref) => {
    const [isVisible, setIsVisible] = React.useState(false);

    const positionClasses = {
      top: "bottom-full left-1/2 -translate-x-1/2 mb-2",
      bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
      left: "right-full top-1/2 -translate-y-1/2 mr-2",
      right: "left-full top-1/2 -translate-y-1/2 ml-2",
    };

    return (
      <div
        ref={ref}
        className="relative inline-block"
        onMouseEnter={() => setIsVisible(true)}
        onMouseLeave={() => setIsVisible(false)}
      >
        {children}
        {isVisible ? (
          <div
            className={cn(
              "absolute z-50 px-2.5 py-1.5 text-xs font-medium text-[#edf3ff] bg-[#1f2a3f] border border-[#2e3d59] rounded-lg shadow-xl whitespace-nowrap animate-in fade-in zoom-in-95 duration-150",
              positionClasses[side],
              className
            )}
          >
            {content}
            <div
              className={cn(
                "absolute w-2 h-2 bg-[#1f2a3f] border-[#2e3d59]",
                side === "top" && "bottom-[-5px] left-1/2 -translate-x-1/2 border-r border-b rotate-45",
                side === "bottom" && "top-[-5px] left-1/2 -translate-x-1/2 border-l border-t rotate-45",
                side === "left" && "right-[-5px] top-1/2 -translate-y-1/2 border-b border-r rotate-45",
                side === "right" && "left-[-5px] top-1/2 -translate-y-1/2 border-l border-t rotate-45"
              )}
            />
          </div>
        ) : null}
      </div>
    );
  }
);
Tooltip.displayName = "Tooltip";

export { Tooltip };
