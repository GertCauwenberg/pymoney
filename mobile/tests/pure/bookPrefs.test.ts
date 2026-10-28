/**
 * Pure logic behind E1.2's book selection: `pickInitialBook` (where a relaunch
 * lands) and `BookPrefs` over `MemoryKvStore` (last-opened + the single
 * offline pin). No React, no native modules — runs on any Node host.
 */
import { describe, expect, it } from "vitest";

import { BookPrefs, pickInitialBook } from "../../src/data/bookPrefs";
import { MemoryKvStore } from "../../src/data/kv";

const A = { id: "book-a" };
const B = { id: "book-b" };

describe("pickInitialBook", () => {
  it("reopens the last-opened book when it is still listed", () => {
    expect(pickInitialBook([A, B], "book-b")).toEqual({ kind: "open", bookId: "book-b" });
    expect(pickInitialBook([A, B], "book-a")).toEqual({ kind: "open", bookId: "book-a" });
  });

  it("ignores a stale last-opened id the user can no longer see", () => {
    expect(pickInitialBook([A, B], "book-gone")).toEqual({ kind: "pick" });
    expect(pickInitialBook([A], "book-gone")).toEqual({ kind: "open", bookId: "book-a" });
  });

  it("opens the sole book without asking", () => {
    expect(pickInitialBook([A], null)).toEqual({ kind: "open", bookId: "book-a" });
    expect(pickInitialBook([A], "")).toEqual({ kind: "open", bookId: "book-a" });
  });

  it("sends an undecided choice to the picker", () => {
    expect(pickInitialBook([A, B], null)).toEqual({ kind: "pick" });
    expect(pickInitialBook([], null)).toEqual({ kind: "pick" });
    expect(pickInitialBook([], "book-a")).toEqual({ kind: "pick" });
  });
});

describe("BookPrefs", () => {
  it("remembers the last opened book across a hydrate (relaunch)", async () => {
    const kv = new MemoryKvStore();
    const first = new BookPrefs(kv);
    await first.hydrate();
    await first.setLastOpenedBookId("book-a");

    const relaunch = new BookPrefs(kv);
    await relaunch.hydrate();
    expect(relaunch.getSnapshot().lastOpenedBookId).toBe("book-a");
    expect(relaunch.getSnapshot().offlinePinBookId).toBeNull();
  });

  it("keeps exactly one offline pin — a new pin replaces the old", async () => {
    const kv = new MemoryKvStore();
    const prefs = new BookPrefs(kv);
    await prefs.hydrate();

    await prefs.setOfflinePinBookId("book-a");
    expect(prefs.getSnapshot().offlinePinBookId).toBe("book-a");

    await prefs.setOfflinePinBookId("book-b");
    expect(prefs.getSnapshot().offlinePinBookId).toBe("book-b");

    await prefs.setOfflinePinBookId(null);
    expect(prefs.getSnapshot().offlinePinBookId).toBeNull();
    expect(await kv.get("offlinePinBookId")).toBeNull();
  });

  it("persists the pin for the next run and notifies subscribers", async () => {
    const kv = new MemoryKvStore();
    const first = new BookPrefs(kv);
    await first.hydrate();
    const seen: (string | null)[] = [];
    first.subscribe(() => seen.push(first.getSnapshot().offlinePinBookId));
    await first.setOfflinePinBookId("book-a");
    expect(seen).toEqual(["book-a"]);

    const relaunch = new BookPrefs(kv);
    await relaunch.hydrate();
    expect(relaunch.getSnapshot().offlinePinBookId).toBe("book-a");
    expect(relaunch.getSnapshot().lastOpenedBookId).toBeNull();
  });

  it("skips a redundant write when nothing changes", async () => {
    const kv = new MemoryKvStore();
    const prefs = new BookPrefs(kv);
    await prefs.hydrate();
    await prefs.setLastOpenedBookId("book-a");
    const seen: unknown[] = [];
    prefs.subscribe(() => seen.push(1));
    await prefs.setLastOpenedBookId("book-a");
    await prefs.setOfflinePinBookId(null);
    expect(seen).toEqual([]);
  });
});
