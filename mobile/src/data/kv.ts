/**
 * Device-local key/value storage (E1.2). Two unrelated things live here:
 * `lastOpenedBookId` (relaunch UX only) and `offlinePinBookId` (the single
 * "Keep this book offline" marker). **Neither is cache policy** — the mirror
 * pull is E5.1's business and is never triggered from here.
 */
export interface KvStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}

/** In-process store for tests and pure logic; nothing persists. */
export class MemoryKvStore implements KvStore {
  private readonly map = new Map<string, string>();

  async get(key: string): Promise<string | null> {
    return this.map.get(key) ?? null;
  }

  async set(key: string, value: string): Promise<void> {
    this.map.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.map.delete(key);
  }
}
