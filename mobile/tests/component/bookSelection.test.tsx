/**
 * E1.2 acceptance against the real `app/` tree (expo-router + the real
 * `RootLayout` wiring), with the wire stubbed at `fetch`:
 * - the last opened book is restored on relaunch
 * - with nothing remembered, the book picker is where you land
 * - a user with no books is linked to onboarding on the web
 * - switching books reloads the tabs on the live API with **no** mirror
 *   re-pull (the `/changes` channel is E5.1's and must stay untouched here)
 * - the "Keep this book offline" pin is visible and at most one book holds it
 *
 * `renderRouter` switches jest to fake timers and keeps them on: wait with
 * `findBy*`/`waitFor` (timer-aware) and interact via `userEvent` with
 * `advanceTimers`. A `Switch` is toggled with `fireEvent(…, "valueChange")` —
 * `userEvent.press` does not reach `onValueChange`.
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
jest.mock("expo-sqlite", () => require("./expoSqliteMock"));

import { fireEvent, screen, userEvent } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { WEB_BASE } from "../../src/api/client";
import { mockKv } from "./expoSqliteMock";

// expo-router's testing-library mocks `expo-linking` itself (its own
// `jest.mock` wins over a competing registration), so spy on the resolved
// module object. The spy must exist before the first test loads the app tree:
// `books.tsx` binds `openURL` at import time.
const Linking = require("expo-linking") as { openURL: (url: string) => Promise<unknown> };
const openURL = jest.spyOn(Linking, "openURL").mockResolvedValue(true);

const CREDENTIALS_KEY = "pymoney.credentials";

const user = {
  id: "0192a000-0000-7000-8000-000000000001",
  email: "gert@example.com",
  display_name: "Gert",
  locale: "en",
};
const bookA = {
  id: "0192a000-0000-7000-8000-00000000000a",
  name: "Groceries",
  role: "owner",
  base_currency_id: "eur",
  revision: 4,
};
const bookB = {
  id: "0192a000-0000-7000-8000-00000000000b",
  name: "Holiday",
  role: "editor",
  base_currency_id: "eur",
  revision: 7,
};

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: { get: () => null },
    json: async () => body,
  } as unknown as Response;
}

type Handler = () => Response;
let mockHandlers: Record<string, Handler> = {};

const mockFetch = jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const method = (init?.method ?? "GET").toUpperCase();
  const path = new URL(String(input)).pathname;
  const handler = mockHandlers[`${method} ${path}`];
  if (!handler) throw new Error(`no stub for ${method} ${path}`);
  return handler();
});

/** Signed-in fixture: session plus the given books, each readable by id. */
function stubBooks(books: typeof bookA[]) {
  mockHandlers = {
    "GET /auth/me": () => json(200, user),
    "GET /books": () => json(200, books),
  };
  for (const b of books) mockHandlers[`GET /books/${b.id}`] = () => json(200, b);
}

const fetchedUrls = () => mockFetch.mock.calls.map(([input]) => String(input));

async function expectHomeBook(name: string) {
  // By text, not testID: a switched-from book stays mounted under the stack.
  expect(await screen.findByText(name)).toBeTruthy();
}

function actor() {
  return userEvent.setup({ advanceTimers: (ms) => jest.advanceTimersByTime(ms) });
}

beforeEach(() => {
  for (const key of Object.keys(mockSecureData)) delete mockSecureData[key];
  for (const key of Object.keys(mockKv)) delete mockKv[key];
  mockHandlers = {};
  mockFetch.mockClear();
  openURL.mockClear();
  globalThis.fetch = mockFetch as unknown as typeof fetch;
});

describe("book selection (E1.2)", () => {
  it("restores the last opened book on relaunch", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    mockKv.lastOpenedBookId = bookB.id;
    stubBooks([bookA, bookB]);

    await renderRouter("app", { initialUrl: "/" });
    await expectHomeBook(bookB.name);
    // Opening a book is a live read, never a changelog pull (E5.1's channel).
    expect(fetchedUrls().some((u) => u.includes(`/books/${bookB.id}`))).toBe(true);
    expect(fetchedUrls().some((u) => u.includes("/changes"))).toBe(false);
  });

  it("opens the picker when no book is remembered and several exist", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    stubBooks([bookA, bookB]);

    await renderRouter("app", { initialUrl: "/" });
    expect(await screen.findByTestId(`book-${bookA.id}`)).toBeTruthy();
    expect(screen.getByTestId(`book-${bookB.id}`)).toBeTruthy();
    expect(screen.queryByTestId("home-book-name")).toBeNull();
  });

  it("opens the sole book without asking", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    stubBooks([bookA]);

    await renderRouter("app", { initialUrl: "/" });
    await expectHomeBook(bookA.name);
  });

  it("links to web onboarding when the user has no books", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    stubBooks([]);

    await renderRouter("app", { initialUrl: "/" });
    expect(await screen.findByText("No books yet.")).toBeTruthy();
    await actor().press(await screen.findByTestId("books-onboarding"));
    expect(openURL).toHaveBeenCalledWith(WEB_BASE);
  });

  it("switching books reloads the tabs without a mirror re-pull", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    mockKv.lastOpenedBookId = bookA.id;
    stubBooks([bookA, bookB]);

    await renderRouter("app", { initialUrl: "/" });
    await expectHomeBook(bookA.name);

    const a = actor();
    await a.press(await screen.findByText("More"));
    await a.press(await screen.findByTestId("more-books"));
    await a.press(await screen.findByTestId(`book-${bookB.id}`));

    await expectHomeBook(bookB.name);
    expect(fetchedUrls().some((u) => u.includes(`/books/${bookB.id}`))).toBe(true);
    expect(fetchedUrls().some((u) => u.includes("/changes"))).toBe(false);
  });

  it("the offline pin is visible in the picker and only one book can hold it", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    stubBooks([bookA, bookB]);

    await renderRouter("app", { initialUrl: "/" });
    const pinA = await screen.findByTestId(`book-pin-${bookA.id}`);
    expect(pinA.props.value).toBe(false);

    await fireEvent(pinA, "valueChange", true);
    expect(screen.getByTestId(`book-pin-${bookA.id}`).props.value).toBe(true);
    expect(screen.getByTestId(`book-pin-${bookB.id}`).props.value).toBe(false);

    // A new pin replaces the previous one — at most one book is pinned.
    await fireEvent(screen.getByTestId(`book-pin-${bookB.id}`), "valueChange", true);
    expect(screen.getByTestId(`book-pin-${bookA.id}`).props.value).toBe(false);
    expect(screen.getByTestId(`book-pin-${bookB.id}`).props.value).toBe(true);

    await fireEvent(screen.getByTestId(`book-pin-${bookB.id}`), "valueChange", false);
    expect(screen.getByTestId(`book-pin-${bookA.id}`).props.value).toBe(false);
    expect(screen.getByTestId(`book-pin-${bookB.id}`).props.value).toBe(false);
  });

  it("the More tab shows the pin for the current book", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    mockKv.lastOpenedBookId = bookA.id;
    mockKv.offlinePinBookId = bookA.id;
    stubBooks([bookA, bookB]);

    await renderRouter("app", { initialUrl: "/" });
    await expectHomeBook(bookA.name);

    const a = actor();
    await a.press(await screen.findByText("More"));
    expect((await screen.findByTestId("more-pin")).props.value).toBe(true);
  });
});
