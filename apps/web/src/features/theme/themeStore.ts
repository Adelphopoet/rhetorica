import { normalizeTheme, THEME_STORAGE_KEY, type ThemePreference, type ResolvedTheme } from "./theme";

type Snapshot = Readonly<{ preference: ThemePreference; resolvedTheme: ResolvedTheme }>;
const SERVER_SNAPSHOT: Snapshot = { preference: "dark", resolvedTheme: "dark" };

export function createThemeStore() {
  let snapshot = SERVER_SNAPSHOT;
  let initialized = false;
  let stopListening: (() => void) | undefined;
  const listeners = new Set<() => void>();

  function update(preference: ThemePreference) {
    const resolvedTheme = preference;
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
    if (snapshot.preference === preference && snapshot.resolvedTheme === resolvedTheme) return;
    snapshot = { preference, resolvedTheme };
    listeners.forEach((listener) => listener());
  }

  function start() {
    if (!initialized) {
      let preference: ThemePreference = "dark";
      try { preference = normalizeTheme(window.localStorage.getItem(THEME_STORAGE_KEY)); } catch { /* Use the default. */ }
      initialized = true;
      update(preference);
    } else {
      update(snapshot.preference);
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      try {
        if (event.storageArea && event.storageArea !== window.localStorage) return;
      } catch { return; }
      // A queued event can be older than this tab's latest selection.
      // The persisted value is the source of truth across tabs.
      try { update(normalizeTheme(window.localStorage.getItem(THEME_STORAGE_KEY))); } catch { /* Keep this session's choice. */ }
    };
    window.addEventListener("storage", onStorage);
    stopListening = () => {
      window.removeEventListener("storage", onStorage);
    };
  }

  return {
    getSnapshot: () => snapshot,
    getServerSnapshot: () => SERVER_SNAPSHOT,
    subscribe(listener: () => void) {
      listeners.add(listener);
      if (listeners.size === 1) start();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) stopListening?.();
      };
    },
    setPreference(preference: ThemePreference) {
      const next = normalizeTheme(preference);
      update(next);
      try { window.localStorage.setItem(THEME_STORAGE_KEY, next); } catch { /* Keep the choice for this session. */ }
    },
  };
}
