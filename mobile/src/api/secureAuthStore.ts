import * as SecureStore from "expo-secure-store";

import { MemoryAuthStore, type AuthStore, type Credentials } from "@pymoney/core";

const KEY = "pymoney.credentials";

/**
 * Bearer credentials in the platform keychain/keystore (M7, README contract).
 *
 * `AuthStore` is synchronous — the HTTP client reads it on every request — so
 * this keeps a memory cache and hydrates it from secure storage at start-up.
 * `ready()` resolves once that read has finished; wait for it before the first
 * authenticated call or a restored token will look signed-out.
 */
export class SecureAuthStore implements AuthStore {
  private readonly cache = new MemoryAuthStore();
  private readonly hydration: Promise<void>;

  constructor() {
    this.hydration = this.hydrate();
  }

  /** Resolves once the keychain has been read into the memory cache. */
  ready(): Promise<void> {
    return this.hydration;
  }

  private async hydrate(): Promise<void> {
    try {
      const raw = await SecureStore.getItemAsync(KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<Credentials>;
      this.cache.set({
        bearer: parsed.bearer ?? undefined,
        csrf: parsed.csrf ?? undefined,
      });
    } catch {
      // Unreadable or corrupt entry: start signed out rather than crash.
    }
  }

  get(): Credentials {
    return this.cache.get();
  }

  set(credentials: Credentials): void {
    this.cache.set(credentials);
    void this.persist(credentials);
  }

  clear(): void {
    this.cache.clear();
    void SecureStore.deleteItemAsync(KEY);
  }

  private async persist(credentials: Credentials): Promise<void> {
    try {
      if (credentials.bearer || credentials.csrf) {
        await SecureStore.setItemAsync(KEY, JSON.stringify(credentials));
      } else {
        await SecureStore.deleteItemAsync(KEY);
      }
    } catch {
      // Write failed; memory still holds the credential for this run.
    }
  }
}
