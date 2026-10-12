"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "./ThemeProvider";

export function ThemeSelector() {
  const { preference, setPreference } = useTheme();
  const isLight = preference === "light";
  const Icon = isLight ? Sun : Moon;
  return (
    <button
      type="button"
      aria-label="Тема интерфейса"
      aria-pressed={isLight}
      title={isLight ? "Включить тёмную тему" : "Включить светлую тему"}
      onClick={() => setPreference(isLight ? "dark" : "light")}
      className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border border-control-border bg-surface px-3 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-hover hover:text-primary focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{isLight ? "Светлая" : "Тёмная"}</span>
    </button>
  );
}
