/** Register and transaction pages against a fake server. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import { AccountRegisterPage } from "../src/ledger/AccountRegisterPage";
import { TransactionPage } from "../src/ledger/TransactionPage";
import type { BookContext } from "../src/pages/BookShell";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: String(minor / 100) });

const accounts = [
  { id: "inc", name: "Income", path: "Income", account_class: "INCOME", type: "INCOME", commodity: "EUR", parent_id: null },
  { id: "sal", name: "Salary", path: "Income:Salary", account_class: "INCOME", type: "INCOME", commodity: "EUR", parent_id: "inc", closed: false, placeholder: false, institution: "" },
  { id: "chk", name: "Checking", path: "Assets:Checking", account_class: "ASSET", type: "CHECKING", commodity: "EUR", parent_id: null },
];
const payees = [{ id: "p1", name: "Acme Payroll" }];

const row = (i: number, extra: object = {}) => ({
  transaction_id: `t${i}`,
  split_id: `s${i}`,
  date: `2026-0${i}-25`,
  memo: "Salary",
  payee_id: "p1",
  account_id: "sal",
  quantity: eur(-300000),
  value: eur(-300000),
  reconcile_state: i === 1 ? "RECONCILED" : "NOT_RECONCILED",
  number: "",
  running_balance: eur(-300000 * i),
  ...extra,
});

function server(handlers: Record<string, (url: URL) => { status?: number; body: unknown }>) {
  const urls: string[] = [];
  const fetch = (async (input: string | URL | Request) => {
    const url = new URL(String(input), "http://test");
    urls.push(url.pathname + url.search);
    const key = Object.keys(handlers).find((k) => url.pathname.endsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: "no route " + url.pathname, status: 404 } }), { status: 404 });
    const { status = 200, body } = handlers[key]!(url);
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { fetch, urls };
}

function mount(fetch: typeof globalThis.fetch, path: string) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur" };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="accounts/:accountId" element={<AccountRegisterPage />} />
            <Route path="transactions/:transactionId" element={<TransactionPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const common = {
  "/accounts": () => ({ body: accounts }),
  "/accounts/sal": () => ({ body: accounts[1] }),
  "/payees": () => ({ body: payees }),
};

describe("AccountRegisterPage", () => {
  it("shows legs with natural signs, the payee, the window from the URL and the closing balance", async () => {
    const { fetch, urls } = server({
      ...common,
      "/register": () => ({
        body: { items: [row(1), row(2)], has_more: false, next_cursor: null, totals: { opening: eur(0), closing: eur(-600000) } },
      }),
    });
    mount(fetch, "/books/b1/accounts/sal?start=2026-01-01&end=2026-02-28");
    expect(await screen.findByRole("heading", { name: "Salary" })).toBeInTheDocument();
    const register = urls.find((u) => u.includes("/register"))!;
    expect(register).toContain("start=2026-01-01");
    expect(register).toContain("end=2026-02-28");
    expect(register).toContain("include_children=false");
    // Income is stored negative and read positive, as in the reports.
    // two legs plus the first running balance
    expect(screen.getAllByText("3,000.00")).toHaveLength(3);
    expect(screen.getAllByText("Acme Payroll")).toHaveLength(2);
    expect(screen.getByText("Balance on 2026-02-28")).toBeInTheDocument();
    expect(screen.getByText("6,000.00 EUR")).toBeInTheDocument();
    expect(screen.getByLabelText("Reconciled")).toBeInTheDocument();
    // Each row links to its transaction.
    expect(screen.getAllByRole("link", { name: "2026-01-25" })[0]).toHaveAttribute("href", "/books/b1/transactions/t1");
    // Breadcrumbs walk the parents.
    expect(screen.getByRole("link", { name: "Income" })).toHaveAttribute("href", "/books/b1/accounts/inc");
  });

  it("pages with the server's cursor and only then shows the closing balance", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({
      ...common,
      "/register": (url) => {
        const cursor = url.searchParams.get("cursor");
        return {
          body: cursor
            ? { items: [row(3)], has_more: false, next_cursor: null, totals: { opening: eur(0), closing: eur(-900000) } }
            : { items: [row(1), row(2)], has_more: true, next_cursor: "opaque-2", totals: { opening: eur(0), closing: eur(-900000) } },
        };
      },
    });
    mount(fetch, "/books/b1/accounts/sal");
    const more = await screen.findByRole("button", { name: "Load more" });
    expect(screen.queryByText("Balance", { selector: "th[scope=row]" })).toBeNull();
    await user.click(more);
    await screen.findByText("2026-03-25");
    expect(urls.some((u) => u.includes("cursor=opaque-2"))).toBe(true);
    expect(screen.getByText("Balance", { selector: "th[scope=row]" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("adds a Value column only when a leg was booked in another commodity", async () => {
    const usd = { commodity_id: "usd", commodity: "USD", minor: 30000, scale: 100, text: "300.00" };
    const { fetch } = server({
      ...common,
      "/register": () => ({
        body: {
          items: [row(1, { quantity: usd, value: eur(27000), running_balance: usd, account_id: "sal" })],
          has_more: false,
          next_cursor: null,
          totals: { opening: null, closing: usd },
        },
      }),
    });
    mount(fetch, "/books/b1/accounts/sal");
    expect(await screen.findByRole("columnheader", { name: "Value" })).toBeInTheDocument();
    // -270.00 EUR stored, flipped for an income account.
    expect(screen.getByText("-270.00 EUR")).toBeInTheDocument();
  });

  it("surfaces the server's error", async () => {
    const { fetch } = server({
      ...common,
      "/register": () => ({ status: 404, body: { error: { type: "NotFoundError", message: "no account", status: 404 } } }),
    });
    mount(fetch, "/books/b1/accounts/sal");
    expect(await screen.findByRole("alert")).toHaveTextContent("no account");
  });
});

describe("TransactionPage", () => {
  const tx = {
    id: "t1",
    date: "2026-01-25",
    currency: "EUR",
    currency_id: "eur",
    memo: "January pay",
    number: "42",
    payee_id: "p1",
    reconcile_state: "CLEARED",
    locked: false,
    revision: 7,
    rounding: "HALF_EVEN",
    entered_at: null,
    imported_id: null,
    splits: [
      { id: "s1", account_id: "chk", memo: "", action: "", number: "", payee_id: null, quantity: eur(300000), value: eur(300000), price: null, implied_price: null, reconcile_state: "CLEARED", reconcile_date: null },
      { id: "s2", account_id: "sal", memo: "gross", action: "", number: "", payee_id: null, quantity: eur(-300000), value: eur(-300000), price: null, implied_price: null, reconcile_state: "NOT_RECONCILED", reconcile_date: null },
    ],
  };

  it("shows the facts and every split, linking accounts back to their registers", async () => {
    const { fetch } = server({ ...common, "/transactions/t1": () => ({ body: tx }) });
    mount(fetch, "/books/b1/transactions/t1");
    expect(await screen.findByRole("heading", { name: "Acme Payroll" })).toBeInTheDocument();
    expect(screen.getByText("January pay")).toBeInTheDocument();
    const splits = screen.getByRole("table");
    expect(within(splits).getByRole("link", { name: "Assets:Checking" })).toHaveAttribute("href", "/books/b1/accounts/chk");
    expect(within(splits).getByRole("link", { name: "Income:Salary" })).toHaveAttribute("href", "/books/b1/accounts/sal");
    expect(within(splits).getByText("3,000.00")).toBeInTheDocument();
    expect(within(splits).getByText("-3,000.00")).toHaveClass("money--negative");
    expect(screen.getByText("Balanced")).toBeInTheDocument();
    expect(screen.getByText("0.00 EUR")).toBeInTheDocument();
  });

  it("says when the server hands back something unbalanced", async () => {
    const broken = { ...tx, splits: [tx.splits[0]!] };
    const { fetch } = server({ ...common, "/transactions/t1": () => ({ body: broken }) });
    mount(fetch, "/books/b1/transactions/t1");
    expect(await screen.findByText("Out of balance by")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("3,000.00 EUR")).toBeInTheDocument());
  });
});
