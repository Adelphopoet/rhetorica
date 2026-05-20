import * as React from "react";
import { cn } from "../../lib/utils";

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  icon?: React.ReactNode;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, label, error, icon, id, ...props }, ref) => {
    const generatedId = React.useId();
    const textareaId = id ?? generatedId;

    return (
      <div className="w-full">
        {label ? (
          <label htmlFor={textareaId} className="block text-sm font-medium text-[#a9b8d4] mb-1.5">
            {label}
          </label>
        ) : null}
        <div className="relative">
          <textarea
            id={textareaId}
            className={cn(
              "flex min-h-[80px] w-full rounded-lg border border-[#2e3d59] bg-[#090e1a] px-3 py-2 text-sm text-[#edf3ff] placeholder:text-[#7484a3] focus:outline-none focus:ring-2 focus:ring-[#5da8ff] focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-y",
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
Textarea.displayName = "Textarea";

export { Textarea };
