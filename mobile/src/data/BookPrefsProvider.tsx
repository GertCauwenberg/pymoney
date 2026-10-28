/**
 * React binding for `BookPrefs` (E1.2). The instance is created once at the
 * root (over `SqliteKvStore`) and hydrated before the first screen renders,
 * so the initial-book redirect never races the read.
 */
import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";

import { type BookPrefs, type BookPrefsSnapshot } from "./bookPrefs";

const BookPrefsContext = createContext<BookPrefs | null>(null);

export function BookPrefsProvider({ prefs, children }: { prefs: BookPrefs; children: ReactNode }) {
  return <BookPrefsContext.Provider value={prefs}>{children}</BookPrefsContext.Provider>;
}

export function useBookPrefs(): BookPrefs {
  const prefs = useContext(BookPrefsContext);
  if (!prefs) throw new Error("useBookPrefs outside a BookPrefsProvider");
  return prefs;
}

/** The live `lastOpenedBookId` / `offlinePinBookId` pair; re-renders on change. */
export function useBookPrefsSnapshot(): BookPrefsSnapshot {
  const prefs = useBookPrefs();
  return useSyncExternalStore(prefs.subscribe, prefs.getSnapshot, prefs.getSnapshot);
}
