/** The graphical reports: slices, lines and cells come from the report payloads unchanged. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { CategoryPieReport } from "../src/reports/CategoryPieReport";
import { HoldingsPieReport } from "../src/reports/HoldingsPieReport";
import { NetWorthReport } from "../src/reports/NetWorthReport";
import { PeriodTableReport } from "../src/reports/PeriodTableReport";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: "" });
const commodities = [
  { id: "eur", code: "EUR", name: "Euro", kind: "CURRENCY", minor_unit_factor: 100 },
  { id: "acme", code: "ACME", name: "Acme", kind: "STOCK", minor_unit_factor: 10000 },
];
const accounts = [
  { id: "inc", account_class: "INCOME" },
  { id: "sal", account_class: "INCOME" },
  { id: "exp", account_class: "EXPENSE" },
  { id: "food", account_class: "EXPENSE" },
  { id: "fuel", account_class: "EXPENSE" },
  { id: "tiny", account_class: "EXPENSE" },
  { id: "tiny2", account_class: "EXPENSE" },
];
const row = (account_id: string, path: string, depth: number, own: number, sub = own) => ({
  account_id,
  path,
  depth,
  value: eur(sub),
  display_value: eur(Math.abs(sub)),
  own_value: eur(own),
});
const statement = {
  start: "2026-01-01",
  end: "2026-03-31",
  target: "EUR",
  income: eur(-300000),
  income_earned: eur(300000),
  expenses: eur(10100),
  net: eur(289900),
  savings_rate: null,
  conversions: [],
  unpriced: [],
  rows: [
    row("inc", "Income", 0, 0, -300000),
    row("sal", "Income:Salary", 1, -300000),
    row("exp", "Expenses", 0, 0, 10100),
    row("food", "Expenses:Food", 1, 6000),
    row("fuel", "Expenses:Fuel", 1, 4000),
    row("tiny", "Expenses:Tiny", 1, 50),
    row("tiny2", "Expenses:Tinier", 1, 50),
  ],
};

function server(handlers: Record<string, (url: URL) => unknown>) {
  const urls: string[] = [];
  const fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    urls.push(url.pathname + url.search);
    const key = Object.keys(handlers).find((k) => url.pathname.endsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: "no route " + url.pathname, status: 404 } }), { status: 404 });
    return new Response(JSON.stringify(handlers[key]!(url)), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { fetch, urls };
}

const reads = {
  "/books/b1/accounts": () => accounts,
  "/books/b1/commodities": () => commodities,
  "/reports/income-statement": () => statement,
};

function mount(fetch: typeof globalThis.fetch, path: string) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role: "viewer" };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="reports/charts/income" element={<CategoryPieReport kind="INCOME" />} />
            <Route path="reports/charts/expenses" element={<CategoryPieReport kind="EXPENSE" />} />
            <Route path="reports/charts/holdings" element={<HoldingsPieReport />} />
            <Route path="reports/charts/net-worth" element={<NetWorthReport />} />
            <Route path="reports/charts/by-period" element={<PeriodTableReport />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("category pies", () => {
  it("slices expenses by each account's own figure, largest first, slivers as Other", async () => {
    const { fetch, urls } = server(reads);
    mount(fetch, "/books/b1/reports/charts/expenses?start=2026-01-01&end=2026-03-31");
    const legend = await screen.findByRole("table");
    const labels = within(legend).getAllByRole("row").map((r) => r.textContent);
    expect(labels[0]).toContain("Food");
    expect(labels[0]).toContain("60.00");
    expect(labels[0]).toContain("59.4%");
    expect(labels[1]).toContain("Fuel");
    expect(labels[2]).toContain("Other");
    expect(labels[2]).toContain("1.00");
    expect(labels[3]).toContain("Total");
    expect(labels[3]).toContain("101.00");
    // Closed accounts always count: a pie must add up to the statement.
    expect(urls.find((u) => u.includes("income-statement"))).toContain("include_closed=true");
    // A slice links to the account's register for the period.
    expect(screen.getByRole("link", { name: "Food" })).toHaveAttribute("href", "/books/b1/accounts/food?start=2026-01-01&end=2026-03-31");
  });

  it("reads income with its natural sign", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/reports/charts/income?start=2026-01-01&end=2026-03-31");
    const legend = await screen.findByRole("table");
    expect(within(legend).getAllByRole("row")[0]!.textContent).toContain("Salary");
    expect(within(legend).getAllByRole("row")[0]!.textContent).toContain("3,000.00");
    expect(within(legend).getAllByRole("row")[0]!.textContent).toContain("100.0%");
  });
});

describe("holdings pie", () => {
  it("slices market values by position", async () => {
    const { fetch, urls } = server({
      ...reads,
      "/reports/holdings": () => ({
        as_of: "2026-03-31",
        target: "EUR",
        total_value: eur(180000),
        total_cost: eur(150000),
        positions: [
          { account_id: "p1", path: "Assets:Broker:ACME", commodity: "ACME", quantity: eur(1), cost: eur(90000), market_value: eur(108000), unrealised_gain: null, unit_price: null },
          { account_id: "p2", path: "Assets:Broker:Other Corp", commodity: "OTHR", quantity: eur(1), cost: eur(60000), market_value: eur(72000), unrealised_gain: null, unit_price: null },
        ],
      }),
    });
    mount(fetch, "/books/b1/reports/charts/holdings?as_of=2026-03-31");
    const legend = await screen.findByRole("table");
    const rows = within(legend).getAllByRole("row").map((r) => r.textContent);
    expect(rows[0]).toContain("ACME (ACME)");
    expect(rows[0]).toContain("60.0%");
    expect(rows[1]).toContain("Other Corp (OTHR)");
    expect(urls.find((u) => u.includes("/reports/holdings"))).toContain("as_of=2026-03-31");
  });
});

describe("net worth over time", () => {
  const series = {
    start: "2026-01-01",
    end: "2026-03-31",
    interval: "month",
    target: "EUR",
    conversions: [],
    unpriced: [],
    points: [
      { as_of: "2026-01-31", label: "2026-01", assets: eur(500000), liabilities: eur(-10000), net_worth: eur(490000) },
      { as_of: "2026-02-28", label: "2026-02", assets: eur(520000), liabilities: eur(-5000), net_worth: eur(515000) },
      { as_of: "2026-03-31", label: "2026-03", assets: eur(560000), liabilities: eur(0), net_worth: eur(560000) },
    ],
  };

  it("draws a point per period and lists them in a table", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({ ...reads, "/reports/net-worth": () => series });
    mount(fetch, "/books/b1/reports/charts/net-worth?start=2026-01-01&end=2026-03-31");
    expect(await screen.findByRole("img", { name: "Line chart" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Period", "Assets", "Liabilities", "Net worth"]);
    expect(rows[1]!.textContent).toContain("2026-01");
    expect(rows[1]!.textContent).toContain("4,900.00");
    // Liabilities are shown as what is owed, positive.
    expect(rows[1]!.textContent).toContain("100.00");
    expect(rows[3]!.textContent).toContain("5,600.00");
    expect(screen.getByText(/change/).textContent).toContain("700.00");
    expect(urls.find((u) => u.includes("net-worth"))).toContain("interval=month");

    await user.selectOptions(screen.getByLabelText("Interval"), "quarter");
    expect(urls.filter((u) => u.includes("net-worth")).at(-1)).toContain("interval=quarter");
  });
});

describe("income and expenses by period", () => {
  it("lays accounts down and periods across, with class totals", async () => {
    const table = {
      start: "2026-01-01",
      end: "2026-02-28",
      interval: "month",
      target: "EUR",
      conversions: [],
      unpriced: [],
      columns: [
        { start: "2026-01-01", end: "2026-01-31", label: "2026-01" },
        { start: "2026-02-01", end: "2026-02-28", label: "2026-02" },
      ],
      rows: [
        { account_id: "exp", path: "Expenses", depth: 0, cells: [eur(8420), eur(4000)], display_cells: [eur(8420), eur(4000)], total: eur(12420), display_total: eur(12420) },
        { account_id: "food", path: "Expenses:Food", depth: 1, cells: [eur(8420), eur(0)], display_cells: [eur(8420), eur(0)], total: eur(8420), display_total: eur(8420) },
      ],
      income: [eur(-320000), eur(0), eur(-320000)],
      income_earned: [eur(320000), eur(0), eur(320000)],
      expenses: [eur(8420), eur(4000), eur(12420)],
      net: [eur(311580), eur(-4000), eur(307580)],
    };
    const { fetch, urls } = server({ ...reads, "/reports/period-table": () => table });
    mount(fetch, "/books/b1/reports/charts/by-period?start=2026-01-01&end=2026-02-28");
    const grid = await screen.findByRole("table");
    const header = within(grid).getAllByRole("columnheader").map((h) => h.textContent);
    expect(header).toEqual(["Account", "2026-01", "2026-02", "Total"]);
    const food = within(grid).getByRole("row", { name: /Food/ });
    // A zero cell is blank; the total is not.
    expect(within(food).getAllByRole("cell").map((c) => c.textContent)).toEqual(["84.20", "", "84.20"]);
    const net = within(grid).getByRole("row", { name: /^Net/ });
    expect(within(net).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3,115.80", "-40.00", "3,075.80"]);
    expect(urls.find((u) => u.includes("period-table"))).toContain("include_closed=false");
    expect(screen.getByRole("link", { name: "Food" })).toHaveAttribute("href", "/books/b1/accounts/food?start=2026-01-01&end=2026-02-28");
  });
});
