import type { ColorScheme } from "@pymoney/tokens";

/** OS scheme name → token scheme. Pure — no React Native (E0.5 pure tests). */
export function resolveScheme(scheme: string | null | undefined): ColorScheme {
  return scheme === "dark" ? "dark" : "light";
}
