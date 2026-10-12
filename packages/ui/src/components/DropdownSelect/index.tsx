"use client";

import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "../../lib/utils";

export type DropdownSelectOption = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

export interface DropdownSelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: DropdownSelectOption[];
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  error?: string;
  icon?: React.ReactNode;
  showDescription?: boolean;
  className?: string;
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
}

/** Controlled UI field. Forms submit application state, not the internal Radix value. */
export const DropdownSelect = React.forwardRef<HTMLButtonElement, DropdownSelectProps>(
  ({ value, onValueChange, options, label, placeholder = "Выберите…", disabled, error, icon, showDescription = false, className, id, ...aria }, ref) => {
    const generatedId = React.useId();
    const triggerId = id ?? generatedId;
    const errorId = `${generatedId}-error`;
    const selected = options.find(option => option.value === value);
    // Prefix every value so the empty selection remains selectable without colliding with real IDs.
    const encode = (optionValue: string) => `option:${optionValue}`;
    return (
      <div className="min-w-0 w-full">
        {label && <label htmlFor={triggerId} className="mb-1.5 block text-sm font-medium text-secondary">{label}</label>}
        <SelectPrimitive.Root value={encode(value)} onValueChange={next => onValueChange(next.slice(7))} disabled={disabled}>
          <SelectPrimitive.Trigger
            {...aria}
            ref={ref}
            id={triggerId}
            aria-describedby={[aria["aria-describedby"], error ? errorId : undefined].filter(Boolean).join(" ") || undefined}
            aria-invalid={error ? true : undefined}
            onPointerDown={event => { if (event.currentTarget.matches(":disabled")) event.preventDefault(); }}
            onKeyDown={event => { if (event.currentTarget.matches(":disabled")) event.preventDefault(); }}
            className={cn(
              "group flex min-h-10 w-full items-center gap-3 rounded-xl border border-control-border bg-surface px-3 py-2 text-left text-sm font-normal normal-case tracking-normal text-primary shadow-sm outline-none transition-colors hover:border-accent/40 hover:bg-hover focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/25 disabled:cursor-not-allowed disabled:opacity-50 data-[state=open]:border-accent/50 data-[state=open]:bg-hover",
              error && "border-danger focus-visible:ring-danger/25", className
            )}
          >
            {icon && <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/15 bg-accent/10 text-accent">{icon}</span>}
            <span className="min-w-0 flex-1">
              <span className="block truncate"><SelectPrimitive.Value>{selected?.label ?? (value || placeholder)}</SelectPrimitive.Value></span>
              {showDescription && selected?.description && <span className="mt-0.5 block truncate text-[11px] text-muted">{selected.description}</span>}
            </span>
            <SelectPrimitive.Icon asChild><ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-muted transition-transform group-data-[state=open]:rotate-180" /></SelectPrimitive.Icon>
          </SelectPrimitive.Trigger>
          <SelectPrimitive.Portal>
            <SelectPrimitive.Content
              position="popper" sideOffset={6} collisionPadding={12}
              aria-label={aria["aria-label"] ?? label}
              aria-labelledby={aria["aria-labelledby"]}
              data-dropdown-select-trigger={triggerId}
              onEscapeKeyDown={event => event.stopPropagation()}
              className="z-[120] max-h-[min(20rem,var(--radix-select-content-available-height))] w-[var(--radix-select-trigger-width)] min-w-[min(16rem,calc(100vw-24px))] max-w-[calc(100vw-24px)] overflow-hidden rounded-xl border border-border-strong bg-surface text-primary shadow-[0_12px_40px_rgb(var(--color-shadow)/0.25)]"
            >
              <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center bg-surface text-muted"><ChevronUp className="h-3 w-3" /></SelectPrimitive.ScrollUpButton>
              <SelectPrimitive.Viewport className="p-1.5">
                {options.map((option, index) => (
                  <SelectPrimitive.Item key={option.value} value={encode(option.value)} textValue={option.label} disabled={option.disabled}
                    aria-describedby={option.description ? `${generatedId}-option-${index}` : undefined}
                    className="relative flex cursor-default select-none items-center gap-3 rounded-lg py-2.5 pl-3 pr-9 text-sm outline-none data-[state=checked]:bg-accent/10 data-[state=checked]:text-accent data-[highlighted]:bg-hover data-[highlighted]:text-primary data-[disabled]:pointer-events-none data-[disabled]:opacity-40"
                  >
                    <span className="min-w-0 flex-1">
                      <SelectPrimitive.ItemText><span className="block break-words font-medium">{option.label}</span></SelectPrimitive.ItemText>
                      {option.description && <span id={`${generatedId}-option-${index}`} className="mt-0.5 block text-xs text-muted">{option.description}</span>}
                    </span>
                    <SelectPrimitive.ItemIndicator className="absolute right-3 text-accent"><Check className="h-4 w-4" aria-hidden="true" /></SelectPrimitive.ItemIndicator>
                  </SelectPrimitive.Item>
                ))}
              </SelectPrimitive.Viewport>
              <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center bg-surface text-muted"><ChevronDown className="h-3 w-3" /></SelectPrimitive.ScrollDownButton>
            </SelectPrimitive.Content>
          </SelectPrimitive.Portal>
        </SelectPrimitive.Root>
        {error && <p id={errorId} role="alert" className="mt-1.5 text-xs text-danger">{error}</p>}
      </div>
    );
  }
);
DropdownSelect.displayName = "DropdownSelect";
