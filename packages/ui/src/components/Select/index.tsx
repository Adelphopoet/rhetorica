import * as React from "react";
import { cn } from "../../lib/utils";
import { ChevronDown } from "lucide-react";

export interface SelectProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: { value: string; label: string; disabled?: boolean }[];
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, label, error, options, id, ...props }, ref) => {
    const generatedId = React.useId();
    const selectId = id ?? generatedId;

    return (
      <div className="w-full">
        {label ? (
          <label htmlFor={selectId} className="block text-sm font-medium text-[#a9b8d4] mb-1.5">
            {label}
          </label>
        ) : null}
        <div className="relative">
          <select
            id={selectId}
            className={cn(
              "flex h-10 w-full appearance-none rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 pr-10 text-sm text-[#edf3ff] focus:outline-none focus:ring-2 focus:ring-[#5da8ff] focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50 transition-all",
              error ? "border-[#ff6d7c] focus:ring-[#ff6d7c]" : "",
              className
            )}
            ref={ref}
            {...props}
          >
            {options.map((option) => (
              <option
                key={option.value}
                value={option.value}
                disabled={option.disabled}
              >
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[#7484a3] pointer-events-none" />
        </div>
        {error ? (
          <p className="mt-1.5 text-xs text-[#ff6d7c]">{error}</p>
        ) : null}
      </div>
    );
  }
);
Select.displayName = "Select";

export { Select };
