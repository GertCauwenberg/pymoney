/**
 * Shape and factory behaviour of the token table. The drift gate lives in
 * `drift.test.ts`; this file only proves the TS surface is coherent.
 */
import { describe, expect, it } from "vitest";

import {
  buildStyle,
  parseGradient,
  rnShadow,
  themeFor,
  tokens,
  type ColorScheme,
  type ColorTokenName,
} from "../src/index";

const COLOR_NAMES: ColorTokenName[] = [
  "bg",
  "surface",
  "surfaceRaised",
  "text",
  "muted",
  "border",
  "borderStrong",
  "accent",
  "accentStrong",
  "accentSoft",
  "accentText",
  "ink",
  "ink2",
  "inkText",
  "negative",
  "negativeSoft",
  "positive",
  "warningBg",
  "warningBorder",
  "warningText",
  "errorBg",
  "errorBorder",
  "errorText",
  "rowHover",
  "rowTint",
];

const HEX = /^#[0-9a-f]{6}$/i;

describe("tokens.json shape", () => {
  it("defines the same colour names for light and dark", () => {
    expect(Object.keys(tokens.color.light).sort()).toEqual(COLOR_NAMES.slice().sort());
    expect(Object.keys(tokens.color.dark).sort()).toEqual(COLOR_NAMES.slice().sort());
  });

  it("every colour is a 6-digit hex", () => {
    for (const scheme of ["light", "dark"] as const) {
      for (const name of COLOR_NAMES) {
        expect(tokens.color[scheme][name], `${scheme}.${name}`).toMatch(HEX);
      }
    }
  });

  it("keeps the decided accent / negative / positive", () => {
    expect(tokens.color.light.accent).toBe("#0b62b8");
    expect(tokens.color.light.negative).toBe("#bc4700");
    expect(tokens.color.light.positive).toBe("#00664f");
  });

  it("exposes spacing and radii as numbers", () => {
    expect(tokens.radius.md).toBe(8);
    expect(tokens.radius.lg).toBe(12);
    expect(tokens.spacing.xs).toBe(4);
    expect(tokens.spacing.xxl).toBe(24);
  });
});

describe("themeFor / buildStyle", () => {
  it("returns the scheme's colours and shared metrics", () => {
    const light = themeFor("light");
    const dark = themeFor("dark");
    expect(light.colors.bg).toBe("#f6f7f9");
    expect(dark.colors.bg).toBe("#0f141b");
    expect(light.radius).toBe(dark.radius);
    expect(light.chart.palette).toEqual(tokens.chart.palette);
  });

  it("buildStyle passes the theme into the factory", () => {
    const styles = buildStyle("dark", (t) => ({
      screen: { backgroundColor: t.colors.bg, padding: t.spacing.lg },
      title: { color: t.colors.accent, fontSize: 28 },
    }));
    expect(styles.screen.backgroundColor).toBe("#0f141b");
    expect(styles.screen.padding).toBe(16);
    expect(styles.title.color).toBe("#64aee8");
  });

  it("parses the topbar gradient into angle + stops", () => {
    const grad = themeFor("light").gradient.topbar;
    expect(grad.angle).toBe(120);
    expect(grad.stops.map((s) => s.color)).toEqual(["#0d2238", "#133a63", "#0b62b8"]);
    expect(grad.stops.map((s) => s.position)).toEqual([0, 55, 130]);
  });

  it("parses the ribbon gradient", () => {
    const ribbon = parseGradient(tokens.gradient.ribbon);
    expect(ribbon.angle).toBe(90);
    expect(ribbon.stops).toHaveLength(4);
    expect(ribbon.stops[0]?.color).toBe("#0b62b8");
  });

  it("rnShadow approximates the first CSS layer", () => {
    const soft = rnShadow(themeFor("light"), "1");
    expect(soft.shadowOffset).toEqual({ width: 0, height: 1 });
    expect(soft.shadowRadius).toBeGreaterThan(0);
    expect(soft.shadowOpacity).toBeGreaterThan(0);
    const raised = rnShadow(themeFor("dark"), "2");
    expect(raised.elevation).toBeGreaterThan(soft.elevation);
  });
});

describe("scheme coverage", () => {
  it("only knows light and dark", () => {
    const schemes = Object.keys(tokens.color) as ColorScheme[];
    expect(schemes.sort()).toEqual(["dark", "light"]);
  });
});
