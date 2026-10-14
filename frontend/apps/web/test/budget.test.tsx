/** The budget report: period table with an editable allowance and a remaining. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import { BudgetReport } from "../src/reports/BudgetReport";
import type { BookContext } from "../src/pages/BookShell";
import { server } from "./server";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: String(minor / 100) });
const commodities = [{ id: "eur", code: "EUR", name: "Euro", kind: "CURRENCY", symbol: "€", minor_unit_factor: 100, display_precision: 2, isin: null, quote_source: null, revision: 1 }];

const report = {
  period: {
    start: "2026-01-01",
    end: "2026-02-28",
    interval: "month",
    target: "EUR",
    columns: [
      { start: "2026-01-01", end: "2026-01-31", label: "2026-01" },
      { start: "2026-02-01", end: "2026-02-28", label: "2026-02" },
    ],
    rows: [
      { account_id: "groc", path: "Expenses:Groceries", depth: 0, cells: [eur(12000), eur(45000)], display_cells: [eur(12000), eur(45000)], total: eur(57000), display_total: eur(57000) },
    ],
    income: [eur(0), eur(0), eur(0)],
    income_earned: [eur(0), eur(0), eur(0)],
    expenses: [eur(12000), eur(45000), eur(57000)],
    net: [eur(-12000), eur(-45000), eur(-57000)],
    conversions: [],
    unpriced: [],
  },
  rows: [{ account_id: "groc", placeholder: false, closed: false, budget: [eur(40000), eur(40000)] }],
  income: { budget: [eur(30000), eur(30000)], spent: [eur(32000), eur(32000)] },
  expenses: { budget: [eur(36000), eur(34000)], spent: [eur(12000), eur(15000)] },
};

function mount(fetch: typeof globalThis.fetch) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role: "owner" };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={["/books/b1/reports/charts/budget"]}>
        <Routes>
          <Route path="/books/:bookId/reports/charts" element={<Outlet context={context} />}>
            <Route path="budget" element={<BudgetReport />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("BudgetReport", () => {
  it("shows spent, the allowance and what is left, and edits a month and the months on screen", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: report }),
      "PATCH /books/b1/accounts/groc/budget": ({ body }) => ({
        body: { id: "bg", account_id: "groc", currency_id: "eur", currency: "EUR", months: {}, revision: 5, ...(body as object) },
      }),
    });
    mount(fetch);

    const row = await screen.findByRole("row", { name: /Groceries/ });
    expect(within(row).getByText("120.00")).toBeInTheDocument(); // Jan spent
    expect(within(row).getByText("280.00")).toBeInTheDocument(); // Jan remaining
    const janInput = within(row).getByLabelText("Budget for Groceries in 2026-01");
    expect(janInput).toHaveValue("400.00");

    // One cell writes one month, and nothing else.
    await user.clear(janInput);
    await user.type(janInput, "500.00");
    janInput.blur();
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ months: { "2026-01": "500.00" } });

    // The "Monthly" column writes every month on screen at once. It is a
    // screen convenience: the plan stores the months, not a running default.
    const monthly = within(row).getByLabelText("Monthly budget for Groceries");
    expect(monthly).toHaveValue("400.00"); // both months agree
    await user.clear(monthly);
    await user.type(monthly, "420");
    monthly.blur();
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH").at(-1)!.body).toEqual({
      months: { "2026-01": "420", "2026-02": "420" },
    });
  });

  it("shows no monthly figure when the months on screen differ", async () => {
    // A figure there means "each of these", so it cannot say that while they
    // differ.
    const mixed = { ...report, rows: [{ ...report.rows[0]!, budget: [eur(40000), eur(60000)] }] };
    const { fetch } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: mixed }),
    });
    mount(fetch);
    const row = await screen.findByRole("row", { name: /Groceries/ });
    expect(within(row).getByLabelText("Monthly budget for Groceries")).toHaveValue("");
  });

  it("shows no monthly figure when nothing is planned", async () => {
    // All blank is blank, which is what "nothing planned" says: the input
    // fills the months on screen, so an empty one means they are all empty.
    const blank = { ...report, rows: [{ ...report.rows[0]!, budget: [null, null] }] };
    const { fetch } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: blank }),
    });
    mount(fetch);
    const row = await screen.findByRole("row", { name: /Groceries/ });
    expect(within(row).getByLabelText("Monthly budget for Groceries")).toHaveValue("");
  });

  it("clearing a month removes that month", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: report }),
      "PATCH /books/b1/accounts/groc/budget": () => ({
        body: { id: "bg", account_id: "groc", currency_id: "eur", currency: "EUR", months: {}, revision: 5 },
      }),
    });
    mount(fetch);

    const row = await screen.findByRole("row", { name: /Groceries/ });
    const janInput = within(row).getByLabelText("Budget for Groceries in 2026-01");
    await user.clear(janInput);
    janInput.blur();
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ months: { "2026-01": null } });
  });

  it("sums the plan into income, expenses and the surplus", async () => {
    const { fetch } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: report }),
    });
    mount(fetch);

    // Income and Expenses appear under both a "Budgeted" and a "Remaining"
    // heading, in that order. Surplus is only under "Budgeted".
    const income = await screen.findAllByRole("row", { name: /^Income/ });
    const expenses = await screen.findAllByRole("row", { name: /^Expenses/ });
    const surplus = await screen.findAllByRole("row", { name: /^Surplus/ });
    expect(income).toHaveLength(2);
    expect(expenses).toHaveLength(2);
    expect(surplus).toHaveLength(1);

    // The server carries plans and movement only, so the footer is derived
    // here: "Remaining" is budget minus spent, Surplus is income minus
    // expenses, and the "Total" column sums the months.
    expect(income[0]).toHaveTextContent("600.00"); // 300 + 300 planned
    expect(income[1]).toHaveTextContent("-40.00"); // (300-320) + (300-320) left
    expect(expenses[0]).toHaveTextContent("700.00"); // 360 + 340 planned
    expect(expenses[1]).toHaveTextContent("430.00"); // (360-120) + (340-150) left
    expect(surplus[0]).toHaveTextContent("-100.00"); // (300-360) + (300-340)

    // Every footer row has the shape of the header row, so the whole-period
    // total at the end sits under the "Total" column.
    const header = await screen.findByRole("row", { name: /Account/ });
    expect(income[0]!.children.length).toBe(header.children.length);
  });

  it("clearing the monthly figure clears the months on screen", async () => {
    // Not the whole plan: a plan holds months, and this writes only the ones
    // the table is showing. A plan left with none of them is removed.
    const user = userEvent.setup();
    const { fetch, calls } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: report }),
      "PATCH /books/b1/accounts/groc/budget": () => ({
        body: { id: "bg", account_id: "groc", currency_id: "eur", currency: "EUR", months: {}, revision: 5 },
      }),
    });
    mount(fetch);
    const row = await screen.findByRole("row", { name: /Groceries/ });
    const monthly = within(row).getByLabelText("Monthly budget for Groceries");
    await user.clear(monthly);
    monthly.blur();
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
      months: { "2026-01": null, "2026-02": null },
    });
  });

  it("offers no editor on a row that cannot carry a plan", async () => {
    // A placeholder only groups its children and a closed account takes
    // nothing new, so the server refuses a plan on either: the inputs stay
    // away rather than swallow what was typed.
    const line = (account_id: string, name: string, depth: number, flags: { placeholder?: boolean; closed?: boolean }) => ({
      period: { account_id, path: `Expenses:${name}`, depth, cells: [eur(0), eur(0)], display_cells: [eur(0), eur(0)], total: eur(0), display_total: eur(0) },
      budget: {
        account_id,
        placeholder: flags.placeholder ?? false,
        closed: flags.closed ?? false,
        budget: [null, null],
        remaining: [null, null],
        total_budget: null,
        total_remaining: null,
      },
    });
    const food = line("food", "Food", 0, { placeholder: true });
    const fuel = line("fuel", "Fuel", 1, { closed: true });
    const groc = line("groc", "Groceries", 1, {});
    const mixed = {
      ...report,
      period: { ...report.period, rows: [food.period, fuel.period, groc.period] },
      rows: [food.budget, fuel.budget, groc.budget],
    };
    const { fetch, calls } = server({
      "GET /books/b1/commodities": () => ({ body: commodities }),
      "GET /books/b1/reports/budget": () => ({ body: mixed }),
    });
    mount(fetch);

    for (const name of ["Food", "Fuel"] as const) {
      const row = await screen.findByRole("row", { name: new RegExp(name) });
      expect(within(row).queryByLabelText(`Budget for ${name} in 2026-01`)).toBeNull();
      expect(within(row).queryByLabelText(`Monthly budget for ${name}`)).toBeNull();
    }
    // A leaf that can carry a plan still gets the editor.
    const leaf = await screen.findByRole("row", { name: /Groceries/ });
    expect(within(leaf).getByLabelText("Budget for Groceries in 2026-01")).toBeInTheDocument();
    expect(within(leaf).getByLabelText("Monthly budget for Groceries")).toBeInTheDocument();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });
});
