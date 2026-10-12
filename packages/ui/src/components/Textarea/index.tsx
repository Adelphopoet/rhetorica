import * as React from "react";
import { cn } from "../../lib/utils";
import "./textarea.css";

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  icon?: React.ReactNode;
  containerClassName?: string;
  innerClassName?: string;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, label, error, icon, id, containerClassName, innerClassName, ...props }, ref) => {
    const generatedId = React.useId();
    const textareaId = id ?? (label ? generatedId : undefined);

    return (
      <div className={cn("w-full", containerClassName)}>
        {label ? (
          <label htmlFor={textareaId} className="block text-sm font-medium text-secondary mb-1.5">
            {label}
          </label>
        ) : null}
        <div className={cn("relative", innerClassName)}>
          <textarea
            id={textareaId}
            className={cn(
"rhetorica-textarea flex min-h-[80px] w-full rounded-lg border border-control-border bg-surface px-3 py-2 text-sm text-primary placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent focus:border-transparent disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-y",
              error ? "border-danger focus:ring-danger" : "",
              className
            )}
            ref={ref}
            {...props}
          />
        </div>
        {error ? (
          <p className="mt-1.5 text-xs text-danger">{error}</p>
        ) : null}
      </div>
    );
  }
);
Textarea.displayName = "Textarea";

export { Textarea };
