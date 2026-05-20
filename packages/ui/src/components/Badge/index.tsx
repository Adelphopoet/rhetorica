import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "bg-[#1f2a3f] text-[#edf3ff]",
        primary: "bg-[#5da8ff]/20 text-[#5da8ff] border border-[#5da8ff]/30",
        success: "bg-[#38c67d]/20 text-[#38c67d] border border-[#38c67d]/30",
        warning: "bg-[#f2b14b]/20 text-[#f2b14b] border border-[#f2b14b]/30",
        danger: "bg-[#ff6d7c]/20 text-[#ff6d7c] border border-[#ff6d7c]/30",
        info: "bg-[#7484a3]/20 text-[#a9b8d4] border border-[#7484a3]/30",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
  ({ className, variant, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(badgeVariants({ variant }), className)}
        {...props}
      />
    );
  }
);
Badge.displayName = "Badge";

export { Badge, badgeVariants };
