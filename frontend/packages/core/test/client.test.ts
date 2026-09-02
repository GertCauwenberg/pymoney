import { ApiClient, ApiError, MemoryAuthStore, queryString } from "../src/client";

interface Call {
  url: string;
  init: RequestInit;
}

function fakeFetch(responses: Array<{ status: number; body?: unknown; headers?: Record<string, string> }>) {
  const calls: Call[] = [];
  const fetch = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    calls.push({ url: String(input), init: init ?? {} });
    const next = responses.shift();
    if (!next) throw new Error("unexpected request " + String(input));
    return new Response(next.body === undefined ? null : JSON.stringify(next.body), {
      status: next.status,
      headers: { "content-type": "application/json", ...next.headers },
    });
  };
  return { fetch: fetch as typeof globalThis.fetch, calls };
}

const session = {
  user: { id: "u", email: "a@b.c", display_name: "A" },
  csrf_token: "csrf-1",
  token: "bearer-1",
  expires_at: "2030-01-01T00:00:00Z",
};

describe("queryString", () => {
  it("skips unset values and repeats arrays", () => {
    expect(queryString({ a: 1, b: undefined, c: null, d: false, e: ["x", "y"], f: "" })).toBe(
      "?a=1&d=false&e=x&e=y&f=",
    );
    expect(queryString({})).toBe("");
  });
});

describe("ApiClient", () => {
  it("strips a trailing slash and builds report URLs", async () => {
    const { fetch, calls } = fakeFetch([{ status: 200, body: { rows: [] } }]);
    const api = new ApiClient({ baseUrl: "/api/", fetch });
    await api.balanceSheet("b1", { as_of: "2026-01-31", include_closed: true, strict: false });
    expect(calls[0]?.url).toBe(
      "/api/books/b1/reports/balance-sheet?as_of=2026-01-31&include_closed=true&strict=false",
    );
    expect(calls[0]?.init.method).toBe("GET");
    expect(calls[0]?.init.credentials).toBe("include");
  });

  it("in cookie mode keeps the CSRF token and sends it only on unsafe methods", async () => {
    const { fetch, calls } = fakeFetch([
      { status: 200, body: session },
      { status: 200, body: [] },
      { status: 204 },
    ]);
    const api = new ApiClient({ baseUrl: "/api", fetch });
    await api.login({ email: "a@b.c", password: "secret-password" });
    expect(api.auth.get()).toEqual({ csrf: "csrf-1", bearer: undefined });

    await api.books();
    const getHeaders = calls[1]?.init.headers as Record<string, string>;
    expect(getHeaders["X-CSRF-Token"]).toBeUndefined();
    expect(getHeaders["Authorization"]).toBeUndefined();

    await api.logout();
    const postHeaders = calls[2]?.init.headers as Record<string, string>;
    expect(postHeaders["X-CSRF-Token"]).toBe("csrf-1");
    expect(api.auth.get()).toEqual({});
  });

  it("in bearer mode keeps the token and sends it everywhere, with no cookies", async () => {
    const { fetch, calls } = fakeFetch([
      { status: 200, body: session },
      { status: 200, body: [] },
    ]);
    const api = new ApiClient({ baseUrl: "https://x", fetch, credentials: "omit", auth: new MemoryAuthStore() });
    await api.login({ email: "a@b.c", password: "secret-password" });
    expect(api.auth.get().bearer).toBe("bearer-1");
    await api.books();
    const headers = calls[1]?.init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer bearer-1");
    expect(headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[1]?.init.credentials).toBe("omit");
  });

  it("turns the server's problem body into an ApiError", async () => {
    const { fetch } = fakeFetch([
      {
        status: 422,
        body: { error: { type: "MissingPriceError", message: "no price for AAPL", status: 422 } },
        headers: { "X-PyMoney-Revision": "51" },
      },
    ]);
    const api = new ApiClient({ baseUrl: "/api", fetch });
    const error = await api.holdings("b1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.type).toBe("MissingPriceError");
    expect(apiError.message).toBe("no price for AAPL");
    expect(apiError.revision).toBe(51);
  });

  it("reads FastAPI validation errors too", async () => {
    const { fetch } = fakeFetch([
      {
        status: 422,
        body: { detail: [{ loc: ["query", "start"], msg: "Field required", type: "missing" }] },
      },
    ]);
    const api = new ApiClient({ baseUrl: "/api", fetch });
    const error = (await api.incomeStatement("b1", { start: "" }).catch((e: unknown) => e)) as ApiError;
    expect(error.type).toBe("RequestValidationError");
    expect(error.message).toBe("start: Field required");
  });

  it("me() recovers the cookie session's CSRF token, but never in bearer mode", async () => {
    const user = { id: "u1", email: "a@b.c", display_name: "A", locale: "en", csrf_token: "recovered" };
    const { fetch } = fakeFetch([{ status: 200, body: user }, { status: 200, body: user }]);
    // Cookie mode: a fresh tab holds the cookie but no token; /me restores it.
    const cookieApi = new ApiClient({ baseUrl: "/api", fetch, credentials: "include" });
    await cookieApi.me();
    expect(cookieApi.auth.get().csrf).toBe("recovered");
    // Bearer mode: no ambient credential, no CSRF token to keep.
    const bearerApi = new ApiClient({ baseUrl: "/api", fetch, credentials: "omit" });
    await bearerApi.me();
    expect(bearerApi.auth.get().csrf).toBeUndefined();
  });

  it("me() never overwrites a token it already holds", async () => {
    const user = { id: "u1", email: "a@b.c", display_name: "A", locale: "en", csrf_token: "stale-from-server" };
    const { fetch } = fakeFetch([{ status: 200, body: user }]);
    const api = new ApiClient({ baseUrl: "/api", fetch, credentials: "include" });
    api.auth.set({ csrf: "fresh-from-login" });
    await api.me();
    expect(api.auth.get().csrf).toBe("fresh-from-login");
  });

  it("reports a 401 to the app -- except from the session probe", async () => {
    const { fetch } = fakeFetch([
      { status: 401, body: { detail: "Not authenticated" } },
      { status: 401, body: { detail: "Not authenticated" } },
    ]);
    const onUnauthorized = vi.fn();
    const api = new ApiClient({ baseUrl: "/api", fetch, onUnauthorized });
    // An ordinary call losing its session drops the app to the login page.
    await expect(api.books()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledOnce();
    // me() *asks* whether a session exists: a 401 is its answer, not an event.
    // Otherwise a cookie-less probe racing a login would wipe the session the
    // login just established.
    await expect(api.me()).rejects.toMatchObject({ status: 401, message: "Not authenticated" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("survives a non-JSON error body", async () => {
    const calls: string[] = [];
    const fetch = (async (input: string | URL | Request) => {
      calls.push(String(input));
      return new Response("<html>Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" });
    }) as typeof globalThis.fetch;
    const api = new ApiClient({ baseUrl: "/api", fetch });
    await expect(api.health()).rejects.toMatchObject({ status: 502, message: "502 Bad Gateway" });
  });
});
