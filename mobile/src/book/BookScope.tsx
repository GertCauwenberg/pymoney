/**
 * The book the tab shell is showing (E1.2). Every per-book query takes this
 * id, so switching books remounts the tabs on a new key — reads reload from
 * the live API for non-pinned books, and nothing here touches the mirror
 * (E5.1 owns cache policy).
 */
import { createContext, useContext, type ReactNode } from "react";

const BookIdContext = createContext<string | null>(null);

export function BookScope({ bookId, children }: { bookId: string; children: ReactNode }) {
  return <BookIdContext.Provider value={bookId}>{children}</BookIdContext.Provider>;
}

export function useBookId(): string {
  const bookId = useContext(BookIdContext);
  if (!bookId) throw new Error("useBookId outside a BookScope");
  return bookId;
}
