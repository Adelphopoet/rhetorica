import * as React from "react";

import { cn } from "../../lib/utils";

type EmptyStateProps = {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
};

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-4 rounded-[24px] border border-dashed border-border-strong bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.9),rgb(var(--color-surface)/0.96))] p-6 text-left",
        className
      )}
    >
      {icon ? (
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-border-strong bg-surface text-accent">
          {icon}
        </div>
      ) : null}
      <div className="space-y-1.5">
        <h3 className="text-base font-semibold text-primary">{title}</h3>
        {description ? <p className="text-sm leading-6 text-secondary">{description}</p> : null}
      </div>
      {action ? <div>{action}</div> : null}
    </div>
  );
}
