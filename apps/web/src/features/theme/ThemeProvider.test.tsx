import { StrictMode, useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { ThemeSelector } from "./ThemeSelector";
import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "./theme";

function Workspace() {
  const { resolvedTheme } = useTheme();
  const [draft, setDraft] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  return <>
    <ThemeSelector />
    <output aria-label="Resolved theme">{resolvedTheme}</output>
    <textarea aria-label="Draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
    <button onClick={() => setModalOpen(true)}>Open modal</button>
    {modalOpen && <div role="dialog" aria-label="Draft details">{draft}</div>}
  </>;
}
function mount() { return render(<StrictMode><ThemeProvider><Workspace /></ThemeProvider></StrictMode>); }

function createMemoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: vi.fn(() => values.clear()),
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    key: vi.fn((index: number) => [...values.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => { values.delete(key); }),
    setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
  };
}

function installStorage(name: "localStorage" | "sessionStorage") {
  const storage = createMemoryStorage();
  Object.defineProperty(window, name, {
    configurable: true,
    get: () => storage,
  });
}

function storageEvent(key: string | null, newValue: string | null, storageArea: Storage) {
  const event = new Event("storage") as StorageEvent;
  Object.defineProperties(event, {
    key: { value: key },
    newValue: { value: newValue },
    storageArea: { value: storageArea },
  });
  return event;
}

function crossTab(key: string | null, newValue: string | null, storageArea: Storage = window.localStorage) {
  if (key === null) storageArea.clear();
  else if (newValue === null) storageArea.removeItem(key);
  else storageArea.setItem(key, newValue);
  fireEvent(window, storageEvent(key, newValue, storageArea));
}

beforeEach(() => {
  installStorage("localStorage");
  installStorage("sessionStorage");
  document.documentElement.dataset.theme = "dark";
  document.documentElement.style.colorScheme = "dark";
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("theme selection", () => {
  it("defaults to dark and renders a direct toggle without a menu", () => {
    const media = vi.spyOn(window, "matchMedia");
    mount();
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveTextContent("Тёмная");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).not.toHaveAttribute("aria-haspopup");
    expect(media).not.toHaveBeenCalled();
  });
  it.each(["light", "dark"])("restores the saved %s theme", (theme) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    mount();
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", String(theme === "light"));
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveTextContent(theme === "light" ? "Светлая" : "Тёмная");
    expect(document.documentElement.dataset.theme).toBe(theme);
    expect(document.documentElement.style.colorScheme).toBe(theme);
  });
  it("switches both ways, persists the last choice and preserves draft and open dialog", async () => {
    const user = userEvent.setup();
    const view = mount();
    await user.type(screen.getByRole("textbox", { name: "Draft" }), "SELECT orders FROM daily");
    await user.click(screen.getByRole("button", { name: "Open modal" }));
    for (const theme of ["light", "dark", "light"]) {
      await user.click(screen.getByRole("button", { name: "Тема интерфейса" }));
      expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe(theme);
      expect(screen.getByRole("dialog")).toHaveTextContent("SELECT orders FROM daily");
      expect(screen.getByRole("textbox")).toHaveValue("SELECT orders FROM daily");
    }
    view.unmount();
    mount();
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
  });
  it("toggles immediately with Enter and Space while retaining keyboard focus", async () => {
    mount();
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Тема интерфейса" });
    await user.tab();
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveAttribute("title", "Включить тёмную тему");
    await user.keyboard(" ");
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button).toHaveAttribute("title", "Включить светлую тему");
    expect(button).toHaveFocus();
  });
  it("does not submit an enclosing form when toggled", async () => {
    const submit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(<ThemeProvider><form onSubmit={submit}><ThemeSelector /></form></ThemeProvider>);
    await userEvent.setup().click(screen.getByRole("button", { name: "Тема интерфейса" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(submit).not.toHaveBeenCalled();
  });
  it("accepts other-tab changes, resets and storage clear without resetting the draft", () => {
    mount();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "keep draft" } });
    crossTab(THEME_STORAGE_KEY, "light");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
    crossTab("unrelated", "dark");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
    crossTab(THEME_STORAGE_KEY, "dark", window.sessionStorage);
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
    crossTab(THEME_STORAGE_KEY, null);
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
    crossTab(THEME_STORAGE_KEY, "light");
    crossTab(null, null);
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("textbox")).toHaveValue("keep draft");
  });
  it("ignores a delayed tab event after a newer local selection", async () => {
    mount();
    crossTab(THEME_STORAGE_KEY, "light");
    await userEvent.setup().click(screen.getByRole("button", { name: "Тема интерфейса" }));
    fireEvent(window, storageEvent(THEME_STORAGE_KEY, "light", window.localStorage));
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
  it("falls back safely on malformed saved and incoming choices", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "system");
    mount();
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
    crossTab(THEME_STORAGE_KEY, "light");
    crossTab(THEME_STORAGE_KEY, "garbage");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "false");
  });
  it("remains usable when localStorage access throws", async () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => { throw new Error("SecurityError"); });
    mount();
    await userEvent.setup().click(screen.getByRole("button", { name: "Тема интерфейса" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
  });
  it("keeps the selected theme for this session when persistence fails", async () => {
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
    mount();
    await userEvent.setup().click(screen.getByRole("button", { name: "Тема интерфейса" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
  it("removes tab listeners on unmount in StrictMode", () => {
    const view = mount();
    crossTab(THEME_STORAGE_KEY, "light");
    view.unmount();
    crossTab(THEME_STORAGE_KEY, "dark");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
  it("hydrates saved light preference without recoverable hydration errors", async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    const app = <ThemeProvider><Workspace /></ThemeProvider>;
    const container = document.createElement("div");
    container.innerHTML = renderToString(app);
    document.body.appendChild(container);
    window.eval(THEME_INIT_SCRIPT);
    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => { root = hydrateRoot(container, app, { onRecoverableError }); });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Тема интерфейса" })).toHaveAttribute("aria-pressed", "true");
    expect(onRecoverableError).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    container.remove();
  });
});
