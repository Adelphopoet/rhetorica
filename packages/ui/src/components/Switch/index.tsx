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
              "w-11 h-6 rounded-full bg-[#1f2a3f] peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-[#5da8ff] peer-focus:ring-offset-2 peer-focus:ring-offset-[#0c1220] transition-colors",
              "peer-checked:bg-[#5da8ff]",
              className
            )}
          />
          <div
            className={cn(
              "absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-[#edf3ff] transition-transform",
              "peer-checked:translate-x-5"
            )}
          />
        </div>
        {label ? (
          <span className="text-sm text-[#a9b8d4]">{label}</span>
        ) : null}
      </label>
    );
  }
);
Switch.displayName = "Switch";

export { Switch };
