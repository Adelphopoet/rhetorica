import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";
import { AlertCircle, CheckCircle, AlertTriangle, Info } from "lucide-react";

const alertVariants = cva(
  "relative w-full rounded-lg border p-4 flex items-start gap-3",
  {
    variants: {
      variant: {
        default: "bg-[#090e1a] border-[#2e3d59] text-[#edf3ff]",
        success: "bg-[#38c67d]/10 border-[#38c67d]/30 text-[#38c67d]",
        warning: "bg-[#f2b14b]/10 border-[#f2b14b]/30 text-[#f2b14b]",
        danger: "bg-[#ff6d7c]/10 border-[#ff6d7c]/30 text-[#ff6d7c]",
        info: "bg-[#5da8ff]/10 border-[#5da8ff]/30 text-[#5da8ff]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

const alertIcons = {
  default: Info,
  success: CheckCircle,
  warning: AlertTriangle,
  danger: AlertCircle,
  info: Info,
};

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {
  title?: string;
  description?: React.ReactNode;
}

const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant, title, description, children, ...props }, ref) => {
    const Icon = alertIcons[variant ?? "default"];

    return (
      <div
        ref={ref}
        role="alert"
        className={cn(alertVariants({ variant }), className)}
        {...props}
      >
        <Icon className="h-5 w-5 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          {title ? (
            <h5 className="font-medium mb-1">{title}</h5>
          ) : null}
          {description ? (
            <div className="text-sm opacity-90">{description}</div>
          ) : null}
          {children}
        </div>
      </div>
    );
  }
);
Alert.displayName = "Alert";

export { Alert, alertVariants };
