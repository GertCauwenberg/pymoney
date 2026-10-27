/**
 * `SecureAuthStore` (E1.1: "login persists across restarts"): the bearer token
 * survives a cold start by living in the platform keychain/keystore, and a
 * corrupt entry must never crash the boot.
 */
const mockSecureData: Record<string, string> = {};
jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async (key: string) =>
    key in mockSecureData ? mockSecureData[key] : null,
  ),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    mockSecureData[key] = value;
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    delete mockSecureData[key];
  }),
}));

import { SecureAuthStore } from "../../src/api/secureAuthStore";

const KEY = "pymoney.credentials";

function settle(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

beforeEach(() => {
  for (const key of Object.keys(mockSecureData)) delete mockSecureData[key];
});

describe("SecureAuthStore", () => {
  it("hydrates a token stored by an earlier run", async () => {
    mockSecureData[KEY] = JSON.stringify({ bearer: "tok-1" });
    const store = new SecureAuthStore();
    await store.ready();
    expect(store.get().bearer).toBe("tok-1");
  });

  it("persists what it is given and a fresh store reads it back", async () => {
    const first = new SecureAuthStore();
    await first.ready();
    first.set({ bearer: "tok-2", csrf: "c-2" });
    await settle();
    const second = new SecureAuthStore();
    await second.ready();
    expect(second.get()).toEqual({ bearer: "tok-2", csrf: "c-2" });
  });

  it("clear removes the stored credential", async () => {
    const store = new SecureAuthStore();
    await store.ready();
    store.set({ bearer: "tok-3" });
    await settle();
    store.clear();
    await settle();
    expect(mockSecureData[KEY]).toBeUndefined();
    const second = new SecureAuthStore();
    await second.ready();
    expect(second.get().bearer).toBeUndefined();
  });

  it("starts signed out when the stored entry is corrupt", async () => {
    mockSecureData[KEY] = "not json";
    const store = new SecureAuthStore();
    await store.ready();
    expect(store.get().bearer).toBeUndefined();
  });
});
