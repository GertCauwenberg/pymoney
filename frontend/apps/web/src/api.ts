import { ApiClient, MemoryAuthStore } from "@pymoney/core";
import type { AuthStore, Credentials } from "@pymoney/core";

/**
 * The web app authenticates with the session cookie, so the only thing worth
 * keeping is the CSRF token -- and it must survive a reload, or the first
 * logout after F5 would be refused. sessionStorage is per tab and dies with it,
 * which matches what a CSRF token is for.
 */
class SessionStorageAuthStore implements AuthStore {
  private static readonly KEY = "pymoney.csrf";
  private readonly fallback = new MemoryAuthStore();

  get(): Credentials {
    try {
      const csrf = window.sessionStorage.getItem(SessionStorageAuthStore.KEY);
      return csrf ? { csrf } : {};
    } catch {
      return this.fallback.get();
    }
  }
  set(credentials: Credentials): void {
    try {
      if (credentials.csrf) window.sessionStorage.setItem(SessionStorageAuthStore.KEY, credentials.csrf);
      else window.sessionStorage.removeItem(SessionStorageAuthStore.KEY);
    } catch {
      this.fallback.set(credentials);
    }
  }
  clear(): void {
    try {
      window.sessionStorage.removeItem(SessionStorageAuthStore.KEY);
    } catch {
      this.fallback.clear();
    }
  }
}

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export function createWebClient(onUnauthorized?: () => void): ApiClient {
  return new ApiClient({
    baseUrl: API_BASE,
    auth: new SessionStorageAuthStore(),
    credentials: "include",
    ...(onUnauthorized ? { onUnauthorized } : {}),
  });
}
