/**
 * E1.1 acceptance against the real `app/` tree (expo-router + the real
 * `RootLayout` wiring), with the wire stubbed at `fetch`:
 * - wrong password shows the server's message verbatim
 * - login persists across restarts (a stored token boots straight into the app)
 * - a mid-session 401 shows "Session expired — sign in to sync" and re-login resumes
 * - the forgot-password request is non-disclosure and links back to login
 * - log out lands on the login screen with the token gone
 *
 * Since E1.2 the signed-in surface is the book shell: the session and log out
 * live on the More tab, and a book name on Home is what "the app resumed"
 * looks like.
 *
 * `renderRouter` switches jest to fake timers and keeps them on: wait with
 * `findBy*` (timer-aware) and interact via `userEvent` with `advanceTimers`.
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

import { screen, userEvent } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

import { mockKv } from "./expoSqliteMock";

const CREDENTIALS_KEY = "pymoney.credentials";

const user = {
  id: "0192a000-0000-7000-8000-000000000001",
  email: "gert@example.com",
  display_name: "Gert",
  locale: "en",
};
const session = {
  user,
  token: "tok-live",
  csrf_token: "csrf-live",
  expires_at: "2099-01-01T00:00:00Z",
};
const book = {
  id: "0192a000-0000-7000-8000-000000000002",
  name: "Personal",
  role: "owner",
  base_currency_id: "eur",
  revision: 4,
};
const books = [book];

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

beforeEach(() => {
  for (const key of Object.keys(mockSecureData)) delete mockSecureData[key];
  for (const key of Object.keys(mockKv)) delete mockKv[key];
  mockHandlers = {};
  mockFetch.mockClear();
  globalThis.fetch = mockFetch as unknown as typeof fetch;
});

function actor() {
  return userEvent.setup({ advanceTimers: (ms) => jest.advanceTimersByTime(ms) });
}

describe("auth flow (E1.1)", () => {
  it("boots into the app with the stored token — login persists across restarts", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-restored" });
    mockHandlers = {
      "GET /auth/me": () => json(200, user),
      "GET /books": () => json(200, books),
      [`GET /books/${book.id}`]: () => json(200, book),
    };
    await renderRouter("app", { initialUrl: "/" });
    expect(await screen.findByText(book.name)).toBeTruthy();
    const me = mockFetch.mock.calls.find(([input]) => String(input).includes("/auth/me"));
    expect(me).toBeTruthy();
    const headers = (me![1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-restored");
  });

  it("shows the server's message verbatim when the password is wrong", async () => {
    mockHandlers = {
      "GET /auth/me": () => json(401, { detail: "Not authenticated" }),
      "POST /auth/login": () => json(401, { detail: "wrong email or password" }),
    };
    await renderRouter("app", { initialUrl: "/" });
    const a = actor();
    await a.type(await screen.findByTestId("login-email"), "gert@example.com");
    await a.type(screen.getByTestId("login-password"), "not-the-password");
    await a.press(screen.getByTestId("login-submit"));
    expect(await screen.findByText("wrong email or password")).toBeTruthy();
    // A failed attempt is the form's error, not a session expiry.
    expect(screen.queryByTestId("session-expired")).toBeNull();
  });

  it("shows the expiry banner on a dead token and re-login resumes", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-dead" });
    mockHandlers = {
      "GET /auth/me": () => json(200, user),
      // Mid-session 401: the books read dies on the expired token.
      "GET /books": () => json(401, { detail: "Not authenticated" }),
    };
    await renderRouter("app", { initialUrl: "/" });

    expect(await screen.findByTestId("session-expired")).toBeTruthy();
    expect(screen.getByText("Session expired — sign in to sync")).toBeTruthy();

    mockHandlers["POST /auth/login"] = () => json(200, session);
    mockHandlers["GET /books"] = () => json(200, books);
    mockHandlers[`GET /books/${book.id}`] = () => json(200, book);
    const a = actor();
    await a.type(await screen.findByTestId("login-email"), "gert@example.com");
    await a.type(screen.getByTestId("login-password"), "correct horse battery");
    await a.press(screen.getByTestId("login-submit"));

    // Resumed: back inside the book shell with live data.
    expect(await screen.findByText(book.name)).toBeTruthy();
    expect(screen.queryByTestId("session-expired")).toBeNull();
  });

  it("requests a reset link without disclosing whether the address exists", async () => {
    mockHandlers = {
      "GET /auth/me": () => json(401, { detail: "Not authenticated" }),
      "POST /auth/forgot-password": () => json(202, { status: "pending", email: "gert@example.com" }),
    };
    await renderRouter("app", { initialUrl: "/" });
    const a = actor();
    await a.press(await screen.findByTestId("login-forgot-link"));

    await a.type(await screen.findByTestId("forgot-email"), "gert@example.com");
    await a.press(screen.getByTestId("forgot-submit"));

    expect(await screen.findByText("Check your email")).toBeTruthy();
    expect(await screen.findByTestId("forgot-sent-email")).toBeTruthy();
    await a.press(screen.getByTestId("forgot-back"));
    expect(await screen.findByTestId("login-submit")).toBeTruthy();
  });

  it("logs out to the login screen and forgets the token", async () => {
    mockSecureData[CREDENTIALS_KEY] = JSON.stringify({ bearer: "tok-live" });
    mockHandlers = {
      "GET /auth/me": () => json(200, user),
      "GET /books": () => json(200, books),
      [`GET /books/${book.id}`]: () => json(200, book),
      "POST /auth/logout": () => json(204, null),
    };
    await renderRouter("app", { initialUrl: "/" });
    const a = actor();
    await a.press(await screen.findByText("More"));
    await a.press(await screen.findByTestId("logout"));
    expect(await screen.findByTestId("login-submit")).toBeTruthy();
    expect(mockSecureData[CREDENTIALS_KEY]).toBeUndefined();
  });
});
