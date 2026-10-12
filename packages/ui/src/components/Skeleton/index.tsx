import * as React from "react";
import { cn } from "../../lib/utils";

const Skeleton = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "animate-pulse rounded-lg bg-gradient-to-r from-hover via-border-strong to-hover bg-[length:200%_100%]",
      className
    )}
    {...props}
  />
));
Skeleton.displayName = "Skeleton";

export { Skeleton };
