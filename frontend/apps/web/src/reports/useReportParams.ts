import { useCallback, useMemo } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";

import type { BookContext } from "../pages/BookShell";

export function useBookContext(): BookContext {
  return useOutletContext<BookContext>();
}

/**
 * Report settings live in the URL, so a report can be bookmarked, shared with
 * the other account holder, or reloaded without losing the date. Defaults are
 * not written to the URL; only what the user changed.
 */
export function useReportParams<T extends Record<string, string | boolean>>(defaults: T) {
  const [search, setSearch] = useSearchParams();

  const params = useMemo(() => {
    const out = { ...defaults } as Record<string, string | boolean>;
    for (const key of Object.keys(defaults)) {
      const raw = search.get(key);
      if (raw === null) continue;
      out[key] = typeof defaults[key] === "boolean" ? raw === "1" || raw === "true" : raw;
    }
    return out as T;
  }, [search, defaults]);

  const update = useCallback(
    (patch: Partial<T>) => {
      setSearch(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            const d = defaults[key];
            if (value === undefined || value === d) next.delete(key);
            else next.set(key, typeof value === "boolean" ? (value ? "1" : "0") : String(value));
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearch, defaults],
  );

  return [params, update] as const;
}
