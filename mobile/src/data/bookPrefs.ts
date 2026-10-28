/**
 * Book-level device preferences (E1.2), decoupled from the wire and from the
 * offline cache:
 *
 * - `lastOpenedBookId` — relaunch UX only. Never cache policy (E5.1 owns that).
 * - `offlinePinBookId` — the single "Keep this book offline" marker. At most
 *   one book is pinned; a new pin replaces the previous one (the mirror wipe
 *   and re-pull that a pin change implies is E5.1's job).
 *
 * Opening a book is navigation plus recording `lastOpenedBookId`; it never
 * triggers a changelog pull.
 */
import type { KvStore } from "./kv";

const LAST_OPENED_KEY = "lastOpenedBookId";
const OFFLINE_PIN_KEY = "offlinePinBookId";

export interface BookPrefsSnapshot {
  lastOpenedBookId: string | null;
  offlinePinBookId: string | null;
}

export class BookPrefs {
  private snapshot: BookPrefsSnapshot = { lastOpenedBookId: null, offlinePinBookId: null };
  private readonly listeners = new Set<() => void>();

  constructor(private readonly kv: KvStore) {}

  /** Read the stored values once, before the first render that needs them. */
  async hydrate(): Promise<void> {
    const [lastOpenedBookId, offlinePinBookId] = await Promise.all([
      this.kv.get(LAST_OPENED_KEY),
      this.kv.get(OFFLINE_PIN_KEY),
    ]);
    this.snapshot = { lastOpenedBookId, offlinePinBookId };
  }

  getSnapshot = (): BookPrefsSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  async setLastOpenedBookId(bookId: string): Promise<void> {
    if (this.snapshot.lastOpenedBookId === bookId) return;
    this.snapshot = { ...this.snapshot, lastOpenedBookId: bookId };
    this.emit();
    try {
      await this.kv.set(LAST_OPENED_KEY, bookId);
    } catch {
      // The value holds for this run; a failed write is not worth a crash.
    }
  }

  /** Pin `bookId` for offline use, or `null` to unpin. A pin replaces the old. */
  async setOfflinePinBookId(bookId: string | null): Promise<void> {
    if (this.snapshot.offlinePinBookId === bookId) return;
    this.snapshot = { ...this.snapshot, offlinePinBookId: bookId };
    this.emit();
    try {
      if (bookId === null) await this.kv.delete(OFFLINE_PIN_KEY);
      else await this.kv.set(OFFLINE_PIN_KEY, bookId);
    } catch {
      // As above: memory keeps the value for this run.
    }
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}

export type InitialBook = { kind: "open"; bookId: string } | { kind: "pick" };

/**
 * Where a signed-in relaunch should land: the last-opened book if the user can
 * still see it, the sole book, otherwise the picker. A stale id (book gone or
 * access revoked) is ignored — never navigated to.
 */
export function pickInitialBook(
  books: readonly { id: string }[],
  lastOpenedBookId: string | null,
): InitialBook {
  if (lastOpenedBookId && books.some((b) => b.id === lastOpenedBookId)) {
    return { kind: "open", bookId: lastOpenedBookId };
  }
  if (books.length === 1) return { kind: "open", bookId: books[0].id };
  return { kind: "pick" };
}
