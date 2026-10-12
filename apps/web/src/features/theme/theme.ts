export type ThemePreference = "light" | "dark";
export type ResolvedTheme = ThemePreference;

export const THEME_STORAGE_KEY = "rhetorica.theme";
export const DEFAULT_THEME: ThemePreference = "dark";

export function normalizeTheme(value: unknown): ThemePreference {
  return value === "light" || value === "dark" ? value : DEFAULT_THEME;
}

// Runs in <head> before the body paints, independently of React hydration.
// Keep this script self-contained so it also works when storage is unavailable.
export const THEME_INIT_SCRIPT = `(function(){var t="dark";try{t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)})||"dark"}catch(e){}if(t!=="light"&&t!=="dark")t="dark";var r=document.documentElement;r.dataset.theme=t;r.style.colorScheme=t;})();`;
