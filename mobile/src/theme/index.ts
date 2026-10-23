/**
 * Mobile theme consumption of `@pymoney/tokens` (E0.3). System colour scheme
 * selects light/dark; every screen styles through `useTheme` / `useThemedStyles`
 * so E8.1 is an audit, not a rewrite.
 */
import { useMemo } from "react";
import { useColorScheme } from "react-native";

import {
  buildStyle,
  rnShadow,
  themeFor,
  type ColorScheme,
  type Theme,
} from "@pymoney/tokens";

import { resolveScheme } from "./scheme";

export type { ColorScheme, Theme };
export { resolveScheme };

/** OS preference → token scheme. */
export function useColorSchemeName(): ColorScheme {
  return resolveScheme(useColorScheme());
}

export function useTheme(): Theme {
  return themeFor(useColorSchemeName());
}

/**
 * `factory` must be stable across renders (module scope) — the memo key is the
 * scheme, not the function identity. Return `StyleSheet.create(…)` from the
 * factory so RN keeps the usual style-object types.
 */
export function useThemedStyles<T>(factory: (theme: Theme) => T): T {
  const scheme = useColorSchemeName();
  return useMemo(() => buildStyle(scheme, factory), [scheme, factory]);
}

export { rnShadow, themeFor, buildStyle };
