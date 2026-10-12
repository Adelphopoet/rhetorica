"use client";

import { type ComponentProps } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import { useTheme } from "@/features/theme/ThemeProvider";

export function ThemedEditor(props: Omit<ComponentProps<typeof Editor>, "theme">) {
  const { resolvedTheme } = useTheme();
  return <Editor {...props} theme={resolvedTheme === "dark" ? "vs-dark" : "light"} />;
}

export function ThemedDiffEditor(props: Omit<ComponentProps<typeof DiffEditor>, "theme">) {
  const { resolvedTheme } = useTheme();
  return <DiffEditor {...props} theme={resolvedTheme === "dark" ? "vs-dark" : "light"} />;
}
