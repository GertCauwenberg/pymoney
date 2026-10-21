/**
 * Drift gate for E0.3: `tokens.json` is the source; `styles.css` and
 * `theme.py` are still hand-maintained (E10.2 generates them). This test fails
 * CI the moment one side changes without the other.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { tokens, type ColorTokenName, type ColorScheme } from "../src/index";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../../..");
const repoRoot = resolve(frontendRoot, "..");
const stylesCssPath = resolve(frontendRoot, "apps/web/src/styles.css");
const themePyPath = resolve(repoRoot, "desktop/src/pymoney_desktop/ui/theme.py");
const chartsTsxPath = resolve(frontendRoot, "apps/web/src/components/charts.tsx");

const cssVarToToken: Record<string, ColorTokenName | "radius.md" | "radius.lg" | "shadow.1" | "shadow.2" | "gradient.topbar" | "font.family"> = {
  "--bg": "bg",
  "--surface": "surface",
  "--surface-raised": "surfaceRaised",
  "--text": "text",
  "--muted": "muted",
  "--border": "border",
  "--border-strong": "borderStrong",
  "--accent": "accent",
  "--accent-strong": "accentStrong",
  "--accent-soft": "accentSoft",
  "--accent-text": "accentText",
  "--ink": "ink",
  "--ink-2": "ink2",
  "--ink-text": "inkText",
  "--negative": "negative",
  "--negative-soft": "negativeSoft",
  "--positive": "positive",
  "--warning-bg": "warningBg",
  "--warning-border": "warningBorder",
  "--warning-text": "warningText",
  "--error-bg": "errorBg",
  "--error-border": "errorBorder",
  "--error-text": "errorText",
  "--row-hover": "rowHover",
  "--row-tint": "rowTint",
  "--radius": "radius.md",
  "--radius-lg": "radius.lg",
  "--shadow-1": "shadow.1",
  "--shadow-2": "shadow.2",
  "--topbar-grad": "gradient.topbar",
  "--font": "font.family",
};

/** theme.py constant name → light-scheme token path. */
const pyToToken: Record<string, ColorTokenName | "chart.palette.1" | "chart.palette"> = {
  BG: "bg",
  SURFACE: "surface",
  TEXT: "text",
  MUTED: "muted",
  BORDER: "border",
  BORDER_STRONG: "borderStrong",
  ACCENT: "accent",
  ACCENT_STRONG: "accentStrong",
  ACCENT_SOFT: "accentSoft",
  INK: "ink",
  INK_2: "ink2",
  INK_TEXT: "inkText",
  NEGATIVE: "negative",
  POSITIVE: "positive",
  ROW_TINT: "rowTint",
  ORANGE: "chart.palette.1",
  CHART_PALETTE: "chart.palette",
};

function read(path: string): string {
  return readFileSync(path, "utf8");
}

/** Extract `--name: value;` pairs from one CSS block body. */
function parseCssBlock(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /(--[a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    const key = m[1];
    const value = m[2]?.trim();
    if (key && value !== undefined) out[key] = value;
  }
  return out;
}

function extractCssSchemes(css: string): Record<ColorScheme, Record<string, string>> {
  const rootMatch = /:root\s*\{([^}]+)\}/.exec(css);
  if (!rootMatch?.[1]) throw new Error("styles.css: no :root block");
  const light = parseCssBlock(rootMatch[1]);

  const darkBlock = /@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]+)\}/.exec(css);
  if (!darkBlock?.[1]) throw new Error("styles.css: no dark :root block");
  const dark = parseCssBlock(darkBlock[1]);

  return { light, dark };
}

function expectedTokenValue(path: string, scheme: ColorScheme): string {
  if (path === "radius.md") return `${tokens.radius.md}px`;
  if (path === "radius.lg") return `${tokens.radius.lg}px`;
  if (path === "shadow.1") return tokens.shadow[scheme]["1"];
  if (path === "shadow.2") return tokens.shadow[scheme]["2"];
  if (path === "gradient.topbar") return tokens.gradient.topbar[scheme];
  if (path === "font.family") return tokens.font.family;
  return tokens.color[scheme][path as ColorTokenName];
}

/** CSS keeps `8px`; the token is the number 8. */
function normalizeCssValue(path: string, cssValue: string): string {
  if (path === "radius.md" || path === "radius.lg") {
    return cssValue.replace(/\s+/g, "");
  }
  return cssValue.replace(/\s+/g, " ").trim();
}

function parsePyConstants(py: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /^([A-Z][A-Z0-9_]*)\s*=\s*"([^"]+)"/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(py)) !== null) {
    const key = m[1];
    const value = m[2];
    if (key && value !== undefined) out[key] = value;
  }
  return out;
}

function parsePyPalette(py: string): string[] {
  const block = /CHART_PALETTE\s*=\s*\(([\s\S]*?)\)/.exec(py);
  if (!block?.[1]) throw new Error("theme.py: no CHART_PALETTE");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1] as string);
}

function parseTsPalette(tsx: string): string[] {
  const block = /export const PALETTE\s*=\s*\[([\s\S]*?)\]/.exec(tsx);
  if (!block?.[1]) throw new Error("charts.tsx: no PALETTE");
  return [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1] as string);
}

describe("design-token drift", () => {
  const css = read(stylesCssPath);
  const py = read(themePyPath);
  const schemes = extractCssSchemes(css);

  it("styles.css :root matches every light colour/radius/shadow/gradient/font token", () => {
    for (const [cssVar, path] of Object.entries(cssVarToToken)) {
      const cssValue = schemes.light[cssVar];
      expect(cssValue, `styles.css light ${cssVar} missing`).toBeDefined();
      expect(
        normalizeCssValue(path, cssValue as string),
        `styles.css light ${cssVar} drifted from tokens.json (${path})`,
      ).toBe(normalizeCssValue(path, expectedTokenValue(path, "light")));
    }
  });

  it("styles.css dark :root matches every dark colour/shadow/gradient token", () => {
    for (const [cssVar, path] of Object.entries(cssVarToToken)) {
      // Radius and font are not redefined in the dark block — they inherit.
      if (path.startsWith("radius.") || path === "font.family") continue;
      const cssValue = schemes.dark[cssVar];
      expect(cssValue, `styles.css dark ${cssVar} missing`).toBeDefined();
      expect(
        normalizeCssValue(path, cssValue as string),
        `styles.css dark ${cssVar} drifted from tokens.json (${path})`,
      ).toBe(normalizeCssValue(path, expectedTokenValue(path, "dark")));
    }
  });

  it("styles.css has no one-sided extra --var outside the token table", () => {
    const known = new Set(Object.keys(cssVarToToken));
    for (const name of Object.keys(schemes.light)) {
      expect(known.has(name), `styles.css defines unmapped ${name}`).toBe(true);
    }
    for (const name of Object.keys(schemes.dark)) {
      // Dark redefines a subset of the light vars.
      expect(known.has(name), `styles.css dark defines unmapped ${name}`).toBe(true);
    }
  });

  it("theme.py light constants match tokens.json (desktop is light-only today)", () => {
    const consts = parsePyConstants(py);
    for (const [name, path] of Object.entries(pyToToken)) {
      if (path === "chart.palette" || path === "chart.palette.1") continue;
      const value = consts[name];
      expect(value, `theme.py ${name} missing`).toBeDefined();
      expect(value, `theme.py ${name} drifted from tokens.json (${path})`).toBe(
        expectedTokenValue(path, "light"),
      );
    }
  });

  it("theme.py CHART_PALETTE matches the token palette (and web charts.tsx)", () => {
    const fromPy = parsePyPalette(py);
    const fromTsx = parseTsPalette(read(chartsTsxPath));
    expect(fromPy).toEqual(tokens.chart.palette);
    expect(fromTsx).toEqual(tokens.chart.palette);
  });

  it("theme.py ORANGE is the chart orange", () => {
    const consts = parsePyConstants(py);
    expect(consts.ORANGE).toBe(tokens.chart.palette[1]);
  });

  it("the brand ribbon in styles.css body::before matches tokens.gradient.ribbon", () => {
    const ribbon = /body::before\s*\{[\s\S]*?background:\s*(linear-gradient\([^;]+\));/.exec(css);
    expect(ribbon?.[1], "styles.css body::before ribbon missing").toBeDefined();
    expect((ribbon?.[1] ?? "").replace(/\s+/g, " ").trim()).toBe(
      tokens.gradient.ribbon.replace(/\s+/g, " ").trim(),
    );
  });
});
