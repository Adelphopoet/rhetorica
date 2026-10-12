// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { describe, expect, it } from "vitest";
import tailwindConfig from "../../../tailwind.config";

const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");
type RGB = [number, number, number];
function palette(selector: string): Record<string, RGB> {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/--color-([\w-]+): ([\d ]+);/g)].map((match) => [match[1], match[2].trim().split(/\s+/).map(Number) as RGB]));
}
function luminance(rgb: RGB): number {
  const linear = rgb.map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(first: RGB, second: RGB): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (values[1] + 0.05) / (values[0] + 0.05);
}
function tint(foreground: RGB, background: RGB, alpha: number): RGB {
  return foreground.map((value, index) => value * alpha + background[index] * (1 - alpha)) as RGB;
}

for (const [theme, selector] of [["dark", ":root"], ["light", 'html[data-theme="light"]']] as const) {
  describe(`${theme} theme contrast`, () => {
    const colors = palette(selector);
    for (const background of ["page", "surface", "elevated", "hover"]) {
      for (const foreground of ["primary", "secondary", "muted", "accent", "success", "warning", "danger", "purple", "cyan", "orange"]) {
        it(`${foreground} text is readable on ${background}`, () => {
          expect(contrast(colors[foreground], colors[background])).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
    for (const status of ["accent", "success", "danger"]) {
      it(`${status} filled button is readable in normal and hover states`, () => {
        expect(contrast(colors["on-accent"], colors[status])).toBeGreaterThanOrEqual(4.5);
        expect(contrast(colors["on-accent"], colors[`${status}-hover`])).toBeGreaterThanOrEqual(4.5);
      });
    }
    for (const status of ["accent", "success", "warning", "danger"]) {
      it(`${status} badge remains readable on its tinted background`, () => {
        expect(contrast(colors[status], tint(colors[status], colors.surface, 0.2))).toBeGreaterThanOrEqual(4.5);
      });
    }
    for (const background of ["page", "surface", "elevated", "hover"]) {
      it(`input outlines and unchecked switches are distinguishable from ${background}`, () => {
        expect(contrast(colors["control-border"], colors[background])).toBeGreaterThanOrEqual(3);
      });
    }
    it("switch thumb remains distinguishable in both checked states", () => {
      expect(contrast(colors["control-thumb"], colors["control-border"])).toBeGreaterThanOrEqual(3);
      expect(contrast(colors["on-accent"], colors.accent)).toBeGreaterThanOrEqual(3);
    });
    it("keyboard focus is distinguishable from input and page backgrounds", () => {
      expect(contrast(colors.accent, colors.surface)).toBeGreaterThanOrEqual(3);
      expect(contrast(colors.accent, colors.page)).toBeGreaterThanOrEqual(3);
    });
  });
}

it("both themes define every exported Tailwind color", () => {
  const dark = palette(":root");
  const light = palette('html[data-theme="light"]');
  const tokens = Object.keys(tailwindConfig.theme?.extend?.colors ?? {});
  expect(Object.keys(dark).sort()).toEqual(tokens.sort());
  expect(Object.keys(light).sort()).toEqual(tokens.sort());
});

it("generates opacity-aware semantic CSS for portals, focus, selection and gradients", async () => {
  const result = await postcss([tailwindcss({
    ...tailwindConfig,
    content: [{ raw: 'bg-surface/90 text-primary border-border-strong focus:ring-accent bg-overlay/[0.78] data-[state=active]:bg-accent data-[state=active]:text-on-accent bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-elevated)/0.94))]', extension: "tsx" }],
  })]).process("@tailwind utilities;", { from: undefined });
  expect(result.css).toContain("rgb(var(--color-surface) / 0.9)");
  expect(result.css).toContain("rgb(var(--color-overlay) / 0.78)");
  expect(result.css).toContain("--color-on-accent");
  expect(result.css).toContain("--color-accent");
  expect(result.css).toContain("linear-gradient(180deg");
});


it("keeps UI color literals out of arbitrary gradients, including underscore stops", () => {
  const roots = [resolve(process.cwd(), "src"), resolve(process.cwd(), "../../packages/ui/src")];
  const violations: string[] = [];
  function visit(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const filename = resolve(directory, entry.name);
      if (entry.isDirectory()) { visit(filename); continue; }
      if (!/\.(tsx|css)$/.test(entry.name) || entry.name.includes(".test.")) continue;
      const source = readFileSync(filename, "utf8");
      // Color libraries legitimately need resolved values; CSS class declarations
      // still must use shared roles, including inside the Mermaid renderer.
      for (const match of source.matchAll(/(?:bg|text|border|ring|fill|stroke|from|via|to)-\[[^\]]*#[0-9a-fA-F]{3,8}[^\]]*\]/g)) {
        violations.push(`${filename}: ${match[0]}`);
      }
    }
  }
  roots.forEach(visit);
  expect(violations).toEqual([]);
});

it("reduces neutral shadow opacity in light mode without changing modal opacity", async () => {
  const darkBody = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
  const lightStart = css.indexOf('html[data-theme="light"] {');
  const lightBody = css.slice(lightStart, css.indexOf("}", lightStart));
  expect(darkBody).toContain("--shadow-opacity-scale: 1;");
  expect(lightBody).toContain("--shadow-opacity-scale: 0.28;");
  const result = await postcss([tailwindcss({
    ...tailwindConfig,
    content: [{ raw: "shadow-[0_24px_80px_rgb(var(--color-shadow)/calc(0.48*var(--shadow-opacity-scale)))] bg-overlay/[0.78]", extension: "tsx" }],
  })]).process("@tailwind utilities;", { from: undefined });
  expect(result.css).toContain("var(--shadow-opacity-scale)");
  expect(result.css).toContain("rgb(var(--color-overlay) / 0.78)");
});


it("native field styles retain the accessible control outline instead of decorative borders", () => {
  const violations: string[] = [];
  function visit(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const filename = resolve(directory, entry.name);
      if (entry.isDirectory()) { visit(filename); continue; }
      if (!entry.name.endsWith(".tsx") || entry.name.includes(".test.")) continue;
      const source = readFileSync(filename, "utf8");
      const fieldClasses = [
        ...source.matchAll(/<(?:input|select|textarea)\b(?:(?!>).)*/gs),
        ...source.matchAll(/(?:const|let)\s+(?:selectClass|inputClass|textareaClass)\s*=\s*["'][^"']+/g),
      ];
      for (const match of fieldClasses) {
        if (/border-border(?:-strong)?(?:\/\d+)?/.test(match[0])) violations.push(filename);
      }
    }
  }
  visit(resolve(process.cwd(), "src"));
  expect(violations).toEqual([]);
});
