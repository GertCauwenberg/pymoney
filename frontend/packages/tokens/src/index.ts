/**
 * Design tokens: one typed view over `tokens.json` (the single source).
 *
 * Non-DOM and non-RN by design — mobile builds StyleSheet objects from these
 * values, web turns them into CSS custom properties (E10.2), desktop paints
 * them into Qt. Do not import `react-native` or CSS APIs here.
 *
 * Paired gate: `test/drift.test.ts` fails CI when `styles.css` or `theme.py`
 * drifts from this table.
 */
import raw from "./tokens.json";

export type ColorScheme = "light" | "dark";

export type ColorTokenName =
  | "bg"
  | "surface"
  | "surfaceRaised"
  | "text"
  | "muted"
  | "border"
  | "borderStrong"
  | "accent"
  | "accentStrong"
  | "accentSoft"
  | "accentText"
  | "ink"
  | "ink2"
  | "inkText"
  | "negative"
  | "negativeSoft"
  | "positive"
  | "warningBg"
  | "warningBorder"
  | "warningText"
  | "errorBg"
  | "errorBorder"
  | "errorText"
  | "rowHover"
  | "rowTint";

export type ColorTokens = Record<ColorTokenName, string>;

export interface ShadowTokens {
  "1": string;
  "2": string;
}

export interface GradientStop {
  color: string;
  /** Position in percent (130 is allowed — the topbar overshoots on purpose). */
  position: number;
}

export interface GradientToken {
  angle: number;
  stops: GradientStop[];
  /** The original `linear-gradient(…)` string, for CSS consumers and drift. */
  css: string;
}

export interface Theme {
  scheme: ColorScheme;
  colors: ColorTokens;
  radius: { sm: number; md: number; lg: number; full: number };
  spacing: { xs: number; sm: number; md: number; lg: number; xl: number; xxl: number };
  shadow: ShadowTokens;
  gradient: { topbar: GradientToken; ribbon: GradientToken };
  font: { family: string };
  chart: { palette: readonly string[] };
}

/** The raw JSON table (validated by `test/tokens.test.ts`). */
export const tokens = raw as {
  color: Record<ColorScheme, ColorTokens>;
  radius: { sm: number; md: number; lg: number; full: number };
  spacing: { xs: number; sm: number; md: number; lg: number; xl: number; xxl: number };
  shadow: Record<ColorScheme, ShadowTokens>;
  gradient: {
    topbar: Record<ColorScheme, string>;
    ribbon: string;
  };
  font: { family: string };
  chart: { palette: string[] };
};

/**
 * Parse `linear-gradient(120deg, #0d2238 0%, …)` into angle + stops so RN
 * gradient components can consume the same source CSS does.
 */
export function parseGradient(css: string): GradientToken {
  const match = /^linear-gradient\((.+)\)$/s.exec(css.trim());
  if (!match || match[1] === undefined) {
    throw new Error(`not a linear-gradient(): ${css}`);
  }
  const parts = splitStops(match[1]);
  const first = parts[0] ?? "";
  const angleMatch = /^([\d.]+)deg$/.exec(first.trim());
  const angle = angleMatch?.[1] !== undefined ? Number(angleMatch[1]) : 180;
  const rawStops = angleMatch ? parts.slice(1) : parts;
  const stops = rawStops.map((part) => {
    const stop = part.trim();
    const at = stop.lastIndexOf(" ");
    if (at < 0) return { color: stop, position: NaN };
    const color = stop.slice(0, at).trim();
    const position = Number(stop.slice(at + 1).replace(/%$/, ""));
    return { color, position };
  });
  return { angle, stops, css };
}

/** Split a gradient body on commas that are not inside `rgba(…)`. */
function splitStops(body: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) {
      out.push(body.slice(start, i));
      start = i + 1;
    }
  }
  out.push(body.slice(start));
  return out.map((s) => s.trim()).filter((s) => s.length > 0);
}

const gradientCache = new Map<string, GradientToken>();

function gradient(css: string): GradientToken {
  const hit = gradientCache.get(css);
  if (hit) return hit;
  const parsed = parseGradient(css);
  gradientCache.set(css, parsed);
  return parsed;
}

/** The theme for one colour scheme. All figures are the tokens, never guesses. */
export function themeFor(scheme: ColorScheme): Theme {
  return {
    scheme,
    colors: tokens.color[scheme],
    radius: tokens.radius,
    spacing: tokens.spacing,
    shadow: tokens.shadow[scheme],
    gradient: {
      topbar: gradient(tokens.gradient.topbar[scheme]),
      ribbon: gradient(tokens.gradient.ribbon),
    },
    font: tokens.font,
    chart: { palette: [...tokens.chart.palette] },
  };
}

/**
 * StyleSheet factory. `factory` receives the theme and returns plain style
 * objects; wrap the result in RN `StyleSheet.create` if you want the usual
 * freeze. Keep `factory` at module scope so call sites can memoise on scheme.
 */
export function buildStyle<T>(scheme: ColorScheme, factory: (theme: Theme) => T): T {
  return factory(themeFor(scheme));
}

/**
 * First-layer approximation of the CSS multi-shadow, for RN `shadow*` /
 * `elevation` props. Web keeps the full multi-layer string in `theme.shadow`.
 */
export function rnShadow(
  theme: Theme,
  level: keyof ShadowTokens,
): {
  shadowColor: string;
  shadowOffset: { width: number; height: number };
  shadowOpacity: number;
  shadowRadius: number;
  elevation: number;
} {
  const css = theme.shadow[level];
  // e.g. "0 1px 2px rgba(16, 33, 54, 0.06), …" → first layer only.
  const first = css.split(",")[0]?.trim() ?? "";
  const m = /^(-?[\d.]+)px\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+(rgba?\([^)]+\))$/.exec(first);
  const y = m?.[2] !== undefined ? Number(m[2]) : 1;
  const blur = m?.[3] !== undefined ? Number(m[3]) : 2;
  const color = m?.[4] ?? "rgba(0, 0, 0, 0.2)";
  const opacityMatch = /,\s*([\d.]+)\)$/.exec(color);
  const opacity = opacityMatch?.[1] !== undefined ? Number(opacityMatch[1]) : 0.2;
  return {
    shadowColor: color,
    shadowOffset: { width: 0, height: y },
    shadowOpacity: opacity,
    shadowRadius: blur / 2,
    elevation: level === "1" ? 2 : 6,
  };
}
