"use client";

import { Badge } from "@rhetorica/ui";
import { CheckCircle, Clock, AlertCircle, ShieldAlert, XCircle } from "lucide-react";

interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const getStatusConfig = (s: string) => {
    switch (s.toLowerCase()) {
      case "success":
      case "completed":
        return { variant: "success" as const, icon: CheckCircle };
      case "running":
      case "pending":
      case "queued":
        return { variant: "info" as const, icon: Clock };
      case "blocked":
        return { variant: "warning" as const, icon: ShieldAlert };
      case "warning":
        return { variant: "warning" as const, icon: AlertCircle };
      case "error":
      case "failed":
        return { variant: "danger" as const, icon: XCircle };
      default:
        return { variant: "default" as const, icon: Clock };
    }
  };

  const config = getStatusConfig(status);
  const Icon = config.icon;

  return (
    <Badge variant={config.variant} className="gap-1.5">
      <Icon className="h-3 w-3" />
      {status}
    </Badge>
  );
}
