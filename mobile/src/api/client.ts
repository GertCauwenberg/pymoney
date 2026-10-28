import { ApiClient, type AuthStore } from "@pymoney/core";

import { SecureAuthStore } from "./secureAuthStore";

/**
 * API origin for the mobile app. Android emulators reach the host machine at
 * 10.0.2.2, not 127.0.0.1 — set `EXPO_PUBLIC_API_BASE` accordingly.
 */
export const API_BASE = process.env.EXPO_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/**
 * The web app — where onboarding lives (book creation, registration). Mobile
 * v1 links there rather than duplicating those flows (E1.2 empty state).
 */
export const WEB_BASE = process.env.EXPO_PUBLIC_WEB_BASE ?? "http://127.0.0.1:5173";

export interface MobileClientOptions {
  baseUrl?: string;
  auth?: AuthStore;
  /**
   * Called on every 401 that is not the `GET /auth/me` probe (that one is how
   * `useSession` asks whether a session exists). Wire this to drop to login;
   * the outbox (E5) must stay untouched when it fires.
   */
  onUnauthorized?: () => void;
}

/**
 * The bearer client (README contract #1): `credentials: "omit"` so a stray
 * cookie can never be the credential that was actually used.
 */
export function createApiClient(options: MobileClientOptions = {}): ApiClient {
  return new ApiClient({
    baseUrl: options.baseUrl ?? API_BASE,
    auth: options.auth ?? new SecureAuthStore(),
    credentials: "omit",
    ...(options.onUnauthorized ? { onUnauthorized: options.onUnauthorized } : {}),
  });
}

export { SecureAuthStore };
