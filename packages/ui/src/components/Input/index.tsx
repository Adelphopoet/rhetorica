import * as React from "react";
import { cn } from "../../lib/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  icon?: React.ReactNode;
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, icon, type, id, ...props }, ref) => {
    const generatedId = React.useId();
    const inputId = id ?? generatedId;

    return (
      <div className="w-full">
        {label ? (
          <label htmlFor={inputId} className="block text-sm font-medium text-[#a9b8d4] mb-1.5">
            {label}
          </label>
        ) : null}
        <div className="relative">
          {icon ? (
            <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[#7484a3]">
              {icon}
            </div>
          ) : null}
          <input
            id={inputId}
            type={type}
            className={cn(
              "flex h-10 w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] placeholder:text-[#7484a3] focus:outline-none focus:ring-2 focus:ring-[#5da8ff] focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50 transition-all",
              icon ? "pl-10" : "",
              error ? "border-[#ff6d7c] focus:ring-[#ff6d7c]" : "",
              className
            )}
            ref={ref}
            {...props}
          />
        </div>
        {error ? (
          <p className="mt-1.5 text-xs text-[#ff6d7c]">{error}</p>
        ) : null}
      </div>
    );
  }
);
Input.displayName = "Input";

export { Input };
