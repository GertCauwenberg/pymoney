/** Prices: the history, hand-entered prices, fetching quotes, and the provider picker. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { CommoditiesPage } from "../src/pages/CommoditiesPage";
import { PricesPage } from "../src/pages/PricesPage";

const commodity = (id: string, code: string, extra: object = {}) => ({
  id,
  code,
  name: code,
  kind: "CURRENCY",
  symbol: null,
  minor_unit_factor: 100,
  display_precision: 2,
  isin: null,
  quote_source: null,
  revision: 1,
  ...extra,
});
const commodities = [
  commodity("eur", "EUR"),
  commodity("usd", "USD", { quote_source: "ecb" }),
  commodity("msft", "MSFT", { kind: "STOCK", minor_unit_factor: 10000, display_precision: 4, quote_source: "yahoo" }),
];
const providers = [
  { name: "ecb", description: "European Central Bank daily reference rates" },
  { name: "yahoo", description: "Yahoo Finance daily closes" },
];
const price = (id: string, extra: object = {}) => ({
  id,
  base_id: "eur",
  base: "EUR",
  quote_id: "usd",
  quote: "USD",
  rate: { num: 5421, den: 5000, approx: "1.0842000000" },
  effective_date: "2026-01-02",
  as_of: "2026-01-02T17:00:00Z",
  source: "ecb",
  revision: 3,
  ...extra,
});
const run = (extra: object = {}) => ({
  started_at: "2026-01-03T06:00:00Z",
  finished_at: "2026-01-03T06:00:02Z",
  start: "2025-12-27",
  end: "2026-01-03",
  requested: 2,
  up_to_date: 0,
  fetched: 5,
  stored: 5,
  unchanged: 0,
  ok: true,
  providers: ["ecb", "yahoo"],
  problems: [],
  summary: "2025-12-27..2026-01-03: asked about 2 commodities, 5 quote(s) received, 5 stored, 0 already known",
  ...extra,
});

type Handler = (req: { url: URL; body: unknown }) => { status?: number; body?: unknown };

function server(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; url: URL }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api/, "");
    calls.push({ method, path, body, url });
    const handler = handlers[`${method} ${path}`];
    if (!handler) {
      return new Response(JSON.stringify({ error: { type: "NotFound", message: `no route ${method} ${path}`, status: 404 } }), { status: 404 });
    }
    const { status = 200, body: out } = handler({ url, body });
    return new Response(out === undefined ? null : JSON.stringify(out), {
      status: out === undefined ? 204 : status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function mount(fetch: typeof globalThis.fetch, path: string, role = "editor") {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="prices" element={<PricesPage />} />
            <Route path="commodities" element={<CommoditiesPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const reads: Record<string, Handler> = {
  "GET /books/b1/commodities": () => ({ body: commodities }),
  "GET /books/b1/accounts": () => ({ body: [] }),
  "GET /quote-providers": () => ({ body: providers }),
  "GET /books/b1/quotes/last": () => ({ body: null }),
  "GET /books/b1/prices": ({ url }) => ({
    body: {
      items: url.searchParams.get("quote_id") === "msft" ? [] : [price("p1"), price("p2", { effective_date: "2026-01-02", source: "user", rate: { num: 11, den: 10, approx: "1.1000000000" }, as_of: "2026-01-02T18:00:00Z" })],
      has_more: false,
      next_cursor: null,
    },
  }),
};

describe("PricesPage", () => {
  it("lists prices newest first with their source and provenance", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/prices");
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    // Same day: the one learned last comes first.
    expect(within(rows[1]!).getByText("1.1")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("user")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("1.0842")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("ecb")).toBeInTheDocument();
    expect(screen.getByText(/Fetched on a schedule for USD \(ecb\), MSFT \(yahoo\)/)).toBeInTheDocument();
  });

  it("adds a hand-entered price as the user's, never rounded", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/prices": ({ body }) => ({ status: 201, body: price("p9", body as object) }),
    });
    mount(fetch, "/books/b1/prices");
    await user.click(await screen.findByRole("button", { name: "Add price" }));
    const form = screen.getByRole("form", { name: "Add price" });
    await user.selectOptions(within(form).getByLabelText("Commodity"), "usd");
    await user.selectOptions(within(form).getByLabelText("In"), "eur");
    await user.type(within(form).getByLabelText("Rate"), "10/11");
    const date = within(form).getByLabelText("Date");
    await user.clear(date);
    await user.type(date, "2026-01-05");
    await user.click(within(form).getByRole("button", { name: "Add price" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add price" })).toBeNull());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      base_id: "usd",
      quote_id: "eur",
      rate: "10/11",
      effective_date: "2026-01-05",
      as_of: null,
      source: "user",
    });
  });

  it("refuses a price of a commodity against itself, and a garbage rate", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server(reads);
    mount(fetch, "/books/b1/prices");
    await user.click(await screen.findByRole("button", { name: "Add price" }));
    const form = screen.getByRole("form", { name: "Add price" });
    await user.type(within(form).getByLabelText("Rate"), "1.1");
    await user.selectOptions(within(form).getByLabelText("Commodity"), "eur");
    expect(within(form).getByText("A price relates two different commodities.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Add price" })).toBeDisabled();
    await user.selectOptions(within(form).getByLabelText("Commodity"), "usd");
    await user.clear(within(form).getByLabelText("Rate"));
    await user.type(within(form).getByLabelText("Rate"), "1,1");
    expect(within(form).getByRole("button", { name: "Add price" })).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("fetches now, shows the report, and backfills from a date", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/quotes/fetch": ({ body }) => ({
        body: (body as { start?: string }).start
          ? run({ start: "2020-01-01", stored: 900, summary: "2020-01-01..2026-01-03: asked about 2 commodities, 900 quote(s) received, 900 stored, 0 already known" })
          : run(),
      }),
    });
    mount(fetch, "/books/b1/prices");
    await user.click(await screen.findByRole("button", { name: "Fetch now" }));
    expect(await screen.findByText(/Just now/)).toBeInTheDocument();
    expect(screen.getByText(/5 stored/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({});

    await user.type(screen.getByLabelText("Backfill from"), "2020-01-01");
    await user.click(screen.getByRole("button", { name: "Backfill" }));
    expect(await screen.findByText(/900 stored/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")[1]!.body).toEqual({ start: "2020-01-01" });
    // Prices are refetched after a run.
    expect(calls.filter((c) => c.method === "GET" && c.path.endsWith("/prices")).length).toBeGreaterThan(2);
  });

  it("shows the last run and its problems, per provider and commodity", async () => {
    const { fetch } = server({
      ...reads,
      "GET /books/b1/quotes/last": () => ({
        body: run({ ok: false, stored: 3, problems: [{ provider: "yahoo", commodity: "MSFT", message: "HTTP 429 Too Many Requests" }] }),
      }),
    });
    mount(fetch, "/books/b1/prices");
    expect(await screen.findByText(/Last run/)).toBeInTheDocument();
    expect(screen.getByText(/HTTP 429 Too Many Requests/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveClass("banner--warning");
  });

  it("deletes a price after confirmation", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({ ...reads, "DELETE /books/b1/prices/p1": () => ({}) });
    mount(fetch, "/books/b1/prices");
    await user.click(await screen.findByRole("button", { name: "Delete EUR/USD 2026-01-02 ecb" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1));
  });

  it("is read-only for a viewer, fetching included", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/prices", "viewer");
    expect(await screen.findByText("1.0842")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add price" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Fetch now" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
  });

  it("filters by a commodity on either side of the pair", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server(reads);
    mount(fetch, "/books/b1/prices");
    await screen.findByText("1.0842");
    await user.selectOptions(screen.getByLabelText("Commodity"), "msft");
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("base_id") === "msft")).toBe(true));
    expect(calls.some((c) => c.url.searchParams.get("quote_id") === "msft")).toBe(true);
  });
});

describe("the commodity editor's quote source", () => {
  it("is a choice of the installed providers, or manual", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "PATCH /books/b1/commodities/usd": ({ body }) => ({ body: { ...commodities[1], ...(body as object) } }),
    });
    mount(fetch, "/books/b1/commodities");
    await user.click(await screen.findByRole("button", { name: "Edit USD" }));
    const form = screen.getByRole("form", { name: "Edit USD" });
    const select = within(form).getByLabelText("Quote source");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Manual (no fetching)", "ecb", "yahoo"]);
    expect(select).toHaveValue("ecb");
    expect(within(form).getByText("European Central Bank daily reference rates")).toBeInTheDocument();
    await user.selectOptions(select, "");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ quote_source: null });
  });

  it("keeps a source whose provider is no longer installed, so it can be cleared", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...reads,
      "GET /books/b1/commodities": () => ({ body: [commodity("eur", "EUR"), commodity("x", "XAU", { quote_source: "goldbug" })] }),
    });
    mount(fetch, "/books/b1/commodities");
    await user.click(await screen.findByRole("button", { name: "Edit XAU" }));
    const select = within(screen.getByRole("form", { name: "Edit XAU" })).getByLabelText("Quote source");
    await waitFor(() => expect(select).toHaveValue("goldbug"));
    expect(screen.getByRole("option", { name: "goldbug (not installed)" })).toBeInTheDocument();
  });
});
