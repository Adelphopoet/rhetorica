import { useEffect } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const currentTheme = vi.hoisted(() => ({ resolvedTheme: "dark" as "dark" | "light" }));
const lifetime = vi.hoisted(() => ({ mounted: vi.fn(), unmounted: vi.fn() }));
vi.mock("@/features/theme/ThemeProvider", () => ({ useTheme: () => currentTheme }));
vi.mock("@monaco-editor/react", () => ({
  default: function MockEditor({ value, theme, onChange, options }: { value: string; theme: string; onChange?: (value: string) => void; options?: { readOnly?: boolean } }) {
    useEffect(() => { lifetime.mounted(); return lifetime.unmounted; }, []);
    return <textarea aria-label="Code" data-theme={theme} value={value} readOnly={options?.readOnly} onChange={event => onChange?.(event.target.value)} />;
  },
  DiffEditor: ({ original, modified, theme, options }: { original: string; modified: string; theme: string; options?: { readOnly?: boolean } }) => {
    useEffect(() => { lifetime.mounted(); return lifetime.unmounted; }, []);
    return <div data-testid="diff" data-theme={theme}><textarea aria-label="Original" value={original} readOnly /><textarea aria-label="Modified" value={modified} readOnly={options?.readOnly} /></div>;
  },
}));

import { ThemedDiffEditor, ThemedEditor } from "./themed-editor";
import { SQLEditor } from "./sql-editor";

describe("themed Monaco editors", () => {
  beforeEach(() => { currentTheme.resolvedTheme = "dark"; lifetime.mounted.mockClear(); lifetime.unmounted.mockClear(); });

  it.each([["sql", "SELECT 1"], ["python", 'result = {"count": 2}']])("preserves %s code, editor model and editing callback when switching", (language, code) => {
    const onChange = vi.fn();
    const view = render(<ThemedEditor language={language} value={code} onChange={onChange} />);
    expect(screen.getByLabelText("Code")).toHaveAttribute("data-theme", "vs-dark");
    currentTheme.resolvedTheme = "light";
    view.rerender(<ThemedEditor language={language} value={code} onChange={onChange} />);
    expect(screen.getByLabelText("Code")).toHaveAttribute("data-theme", "light");
    expect(screen.getByLabelText("Code")).toHaveValue(code);
    expect(onChange).not.toHaveBeenCalled();
    expect(lifetime.mounted).toHaveBeenCalledTimes(1);
    expect(lifetime.unmounted).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: `${code}\n` } });
    expect(onChange).toHaveBeenCalledWith(`${code}\n`);
    currentTheme.resolvedTheme = "dark";
    view.rerender(<ThemedEditor language={language} value={`${code}\n`} onChange={onChange} />);
    expect(screen.getByLabelText("Code")).toHaveAttribute("data-theme", "vs-dark");
    expect(screen.getByLabelText("Code")).toHaveValue(`${code}\n`);
  });

  it("preserves the AI proposal diff and read-only mode", () => {
    const view = render(<ThemedDiffEditor language="json" original='{"nodes":[]}' modified='{"nodes":[1]}' options={{ readOnly: true }} />);
    currentTheme.resolvedTheme = "light";
    view.rerender(<ThemedDiffEditor language="json" original='{"nodes":[]}' modified='{"nodes":[1]}' options={{ readOnly: true }} />);
    expect(screen.getByTestId("diff")).toHaveAttribute("data-theme", "light");
    expect(screen.getByLabelText("Original")).toHaveValue('{"nodes":[]}');
    expect(screen.getByLabelText("Modified")).toHaveValue('{"nodes":[1]}');
    expect(screen.getByLabelText("Modified")).toHaveAttribute("readonly");
    expect(lifetime.mounted).toHaveBeenCalledTimes(1);
    expect(lifetime.unmounted).not.toHaveBeenCalled();
  });

  it("updates the generated SQL preview theme while preserving SQL and read-only behavior", () => {
    const view = render(<SQLEditor sql="SELECT count(*) FROM orders" />);
    currentTheme.resolvedTheme = "light";
    view.rerender(<SQLEditor sql="SELECT count(*) FROM orders" />);
    expect(screen.getByLabelText("Code")).toHaveAttribute("data-theme", "light");
    expect(screen.getByLabelText("Code")).toHaveValue("SELECT count(*) FROM orders");
    expect(screen.getByLabelText("Code")).toHaveAttribute("readonly");
    expect(lifetime.mounted).toHaveBeenCalledTimes(1);
  });
});
