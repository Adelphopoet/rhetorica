import * as React from "react";
import { cn } from "../../lib/utils";

const Skeleton = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "animate-pulse rounded-lg bg-gradient-to-r from-[#1f2a3f] via-[#2e3d59] to-[#1f2a3f] bg-[length:200%_100%]",
      className
    )}
    {...props}
  />
));
Skeleton.displayName = "Skeleton";

export { Skeleton };
