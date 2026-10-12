import * as React from "react";
import { cn } from "../../lib/utils";

export interface SwitchProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

const Switch = React.forwardRef<HTMLInputElement, SwitchProps>(
  ({ className, label, ...props }, ref) => {
    return (
      <label className="flex items-center gap-3 cursor-pointer">
        <div className="relative">
          <input
            type="checkbox"
            className="sr-only peer"
            ref={ref}
            {...props}
          />
          <div
            className={cn(
              "w-11 h-6 rounded-full bg-control-border peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-accent peer-focus:ring-offset-2 peer-focus:ring-offset-surface transition-colors",
              "peer-checked:bg-accent",
              className
            )}
          />
          <div
            className={cn(
              "absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-control-thumb peer-checked:bg-on-accent shadow-sm transition-transform",
              "peer-checked:translate-x-5"
            )}
          />
        </div>
        {label ? (
          <span className="text-sm text-secondary">{label}</span>
        ) : null}
      </label>
    );
  }
);
Switch.displayName = "Switch";

export { Switch };
