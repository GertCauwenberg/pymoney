/**
 * The report pages against a fake server: the query string sent, the URL
 * state, the error path. The fetch is faked at the ApiClient boundary, so
 * this is the real client, real hooks, real components.
 */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BalanceSheetReport } from "../src/reports/BalanceSheetReport";
import { IncomeStatementReport } from "../src/reports/IncomeStatementReport";
import type { BookContext } from "../src/pages/BookShell";
import { Outlet } from "react-router-dom";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: "" });

const accounts = [
  { id: "a", account_class: "ASSET" },
  { id: "i", account_class: "INCOME" },
  { id: "x", account_class: "EXPENSE" },
];
const currencies = [
  { id: "eur", code: "EUR", name: "Euro", kind: "CURRENCY" },
  { id: "usd", code: "USD", name: "US Dollar", kind: "CURRENCY" },
];

function server(handlers: Record<string, (url: URL) => { status?: number; body: unknown }>) {
  const urls: string[] = [];
  const fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    urls.push(url.pathname + url.search);
    const key = Object.keys(handlers).find((k) => url.pathname.endsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: "no route", status: 404 } }), { status: 404 });
    const { status = 200, body } = handlers[key]!(url);
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { fetch, urls };
}

function mount(ui: React.ReactNode, fetch: typeof globalThis.fetch, path: string) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur" };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/r" element={ui} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("BalanceSheetReport", () => {
  it("asks for the date in the URL and renders the payload", async () => {
    const { fetch, urls } = server({
      "/accounts": () => ({ body: accounts }),
      "/commodities": () => ({ body: currencies }),
      "/reports/balance-sheet": () => ({
        body: {
          as_of: "2026-03-31",
          target: "EUR",
          assets: eur(1000),
          liabilities: eur(0),
          liabilities_owed: eur(0),
          equity: eur(-1000),
          net_worth: eur(1000),
          rows: [{ account_id: "a", path: "Assets", depth: 0, value: eur(1000), display_value: eur(1000), own_value: eur(1000) }],
          conversions: [],
          unpriced: ["BTC"],
        },
      }),
    });
    mount(<BalanceSheetReport />, fetch, "/r?as_of=2026-03-31&closed=1");
    expect(await screen.findByText("Net worth")).toBeInTheDocument();
    const query = urls.find((u) => u.includes("/reports/balance-sheet"))!;
    expect(query).toContain("as_of=2026-03-31");
    expect(query).toContain("include_closed=true");
    expect(query).toContain("strict=false");
    expect(query).not.toContain("target_id");
    expect(screen.getByText(/left out of the totals/i).closest(".banner")).toHaveTextContent("BTC");
    expect(screen.getByText("As of 2026-03-31 · in EUR")).toBeInTheDocument();
  });

  it("shows the server's explanation when the report fails", async () => {
    const { fetch } = server({
      "/accounts": () => ({ body: accounts }),
      "/commodities": () => ({ body: currencies }),
      "/reports/balance-sheet": () => ({
        status: 422,
        body: { error: { type: "MissingPriceError", message: "no price for BTC in EUR", status: 422 } },
      }),
    });
    mount(<BalanceSheetReport />, fetch, "/r?strict=1");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The report could not be produced");
    expect(alert).toHaveTextContent("no price for BTC in EUR");
  });

  it("re-queries in another currency when one is chosen", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({
      "/accounts": () => ({ body: accounts }),
      "/commodities": () => ({ body: currencies }),
      "/reports/balance-sheet": (url) => ({
        body: {
          as_of: "2026-03-31",
          target: url.searchParams.get("target_id") === "usd" ? "USD" : "EUR",
          assets: eur(1),
          liabilities: eur(0),
          liabilities_owed: eur(0),
          equity: eur(-1),
          net_worth: eur(1),
          rows: [],
          conversions: [],
          unpriced: [],
        },
      }),
    });
    mount(<BalanceSheetReport />, fetch, "/r?as_of=2026-03-31");
    await screen.findByText("As of 2026-03-31 · in EUR");
    await user.selectOptions(await screen.findByRole("combobox"), "usd");
    await screen.findByText("As of 2026-03-31 · in USD");
    expect(urls.some((u) => u.includes("target_id=usd"))).toBe(true);
  });
});

describe("IncomeStatementReport", () => {
  it("uses a preset period, writes it to the URL, and shows the savings rate", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({
      "/accounts": () => ({ body: accounts }),
      "/commodities": () => ({ body: currencies }),
      "/reports/income-statement": (url) => ({
        body: {
          start: url.searchParams.get("start"),
          end: url.searchParams.get("end"),
          target: "EUR",
          income: eur(-400000),
          income_earned: eur(400000),
          expenses: eur(100000),
          net: eur(300000),
          savings_rate: { num: 3, den: 4, approx: "0.75" },
          rows: [
            { account_id: "i", path: "Income", depth: 0, value: eur(-400000), display_value: eur(400000), own_value: eur(-400000) },
            { account_id: "x", path: "Expenses", depth: 0, value: eur(100000), display_value: eur(100000), own_value: eur(100000) },
          ],
          conversions: [],
          unpriced: [],
        },
      }),
    });
    mount(<IncomeStatementReport />, fetch, "/r");
    expect(await screen.findByText(/^75\.0\s%$/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Last year" }));
    const lastYear = new Date().getFullYear() - 1;
    await waitFor(() =>
      expect(urls.some((u) => u.includes(`start=${lastYear}-01-01&end=${lastYear}-12-31`))).toBe(true),
    );
    expect(screen.getByRole("button", { name: "Last year" })).toHaveAttribute("aria-pressed", "true");
  });

  it("refuses a period that ends before it starts without calling the server", async () => {
    const { fetch, urls } = server({
      "/accounts": () => ({ body: accounts }),
      "/commodities": () => ({ body: currencies }),
      "/reports/income-statement": () => ({ body: {} }),
    });
    mount(<IncomeStatementReport />, fetch, "/r?start=2026-02-01&end=2026-01-01");
    expect(await screen.findByRole("alert")).toHaveTextContent(/ends before it starts/);
    await waitFor(() => expect(urls.some((u) => u.includes("/accounts"))).toBe(true));
    expect(urls.some((u) => u.includes("/reports/income-statement"))).toBe(false);
  });
});
