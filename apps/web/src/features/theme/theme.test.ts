import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { normalizeTheme, THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "./theme";

function bootstrap(stored: unknown, storageBlocked = false) {
  const root = { dataset: {} as Record<string, string>, style: {} as Record<string, string> };
  const getItem = vi.fn(() => { if (storageBlocked) throw new Error("SecurityError"); return stored; });
  // No window/matchMedia or React: initialization must finish before hydration.
  runInNewContext(THEME_INIT_SCRIPT, { localStorage: { getItem }, document: { documentElement: root } });
  expect(getItem).toHaveBeenCalledWith(THEME_STORAGE_KEY);
  return root;
}

describe("theme before first paint", () => {
  it.each(["light", "dark"] as const)("restores %s without React or OS preferences", (theme) => {
    expect(bootstrap(theme)).toEqual({ dataset: { theme }, style: { colorScheme: theme } });
  });
  it.each([null, undefined, "", "system", "LIGHT", "unexpected", "<script>alert(1)</script>"])("falls back to dark for %s", (value) => {
    expect(normalizeTheme(value)).toBe("dark");
    expect(bootstrap(value).dataset.theme).toBe("dark");
  });
  it("paints dark when storage is blocked", () => {
    expect(bootstrap(null, true)).toEqual({ dataset: { theme: "dark" }, style: { colorScheme: "dark" } });
  });
});
