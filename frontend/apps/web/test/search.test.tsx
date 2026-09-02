/** Finding transactions: criteria become the query string, results link through. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import { SearchPage, describeAccounts } from "../src/ledger/SearchPage";
import type { BookContext } from "../src/pages/BookShell";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: "" });
const accounts = [
  { id: "chk", name: "Checking", path: "Assets:Checking", account_class: "ASSET", type: "CHECKING", commodity_id: "eur", commodity: "EUR", parent_id: null, closed: false, placeholder: false },
  { id: "food", name: "Food", path: "Expenses:Food", account_class: "EXPENSE", type: "EXPENSE", commodity_id: "eur", commodity: "EUR", parent_id: null, closed: false, placeholder: false },
];
const payees = [{ id: "p1", name: "Acme Groceries", notes: "", address: "", match_keys: [], default_account_id: null, revision: 1 }];
const commodities = [{ id: "eur", code: "EUR", name: "Euro", kind: "CURRENCY", minor_unit_factor: 100 }];
const split = (id: string, account_id: string, minor: number) => ({
  id, account_id, quantity: eur(minor), value: eur(minor), memo: "", payee_id: null, reconcile_state: "NOT_RECONCILED", reconcile_date: null, action: "", number: "", price: null, implied_price: null,
});
const tx = {
  id: "t1", date: "2026-01-05", currency_id: "eur", currency: "EUR", memo: "Supermarket", payee_id: "p1", number: "", rounding: "HALF_EVEN",
  entered_at: null, imported_id: null, reconcile_state: "CLEARED", locked: false, revision: 3,
  splits: [split("s1", "chk", -8420), split("s2", "food", 8420)],
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

function mount(fetch: typeof globalThis.fetch, path: string) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role: "viewer" };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="search" element={<SearchPage />} />
            <Route path="transactions/:transactionId" element={<p>transaction page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const reads = {
  "/books/b1/accounts": () => accounts,
  "/books/b1/payees": () => payees,
  "/books/b1/commodities": () => commodities,
};

describe("SearchPage", () => {
  it("sends nothing until there is a criterion, then the criteria as the query string", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({
      ...reads,
      "/transactions/search": (url) => ({
        items: url.searchParams.get("q") === "super" ? [tx] : [],
        has_more: false,
        limit: 200,
      }),
    });
    mount(fetch, "/books/b1/search");
    await screen.findByRole("heading", { name: "Find transactions" });
    expect(urls.some((u) => u.includes("/transactions/search"))).toBe(false);

    await user.type(screen.getByLabelText("Text"), "super");
    const row = await screen.findByRole("row", { name: /Supermarket/ });
    expect(within(row).getByText("Checking → Food")).toBeInTheDocument();
    expect(within(row).getByText("84.20")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "2026-01-05" })).toHaveAttribute("href", "/books/b1/transactions/t1");
    expect(screen.getByRole("status")).toHaveTextContent("One transaction matches.");
    const sent = urls.filter((u) => u.includes("/transactions/search")).at(-1)!;
    expect(sent).toContain("q=super");
    expect(sent).toContain("limit=200");

    // More criteria narrow: they all travel, and a blank amount does not.
    await user.type(screen.getByLabelText("Smallest amount"), "50");
    await user.selectOptions(screen.getByLabelText("Account"), "food");
    await user.click(screen.getByLabelText("Include sub-accounts"));
    await waitFor(() => {
      const last = urls.filter((u) => u.includes("/transactions/search")).at(-1)!;
      expect(last).toContain("amount_min=50");
      expect(last).toContain("account_id=food");
      expect(last).toContain("include_children=true");
      expect(last).not.toContain("amount_max");
    });
    // Clicking through lands on the transaction.
    await user.click(within(row).getByRole("link", { name: "2026-01-05" }));
    expect(await screen.findByText("transaction page")).toBeInTheDocument();
  });

  it("refuses nonsense locally and reports the server's answer", async () => {
    const user = userEvent.setup();
    const { fetch, urls } = server({
      ...reads,
      "/transactions/search": () => ({ items: [], has_more: true, limit: 200 }),
    });
    mount(fetch, "/books/b1/search?min=10&max=1");
    expect(await screen.findByRole("alert")).toHaveTextContent("smallest amount is larger");
    expect(urls.some((u) => u.includes("/transactions/search"))).toBe(false);
    await user.clear(screen.getByLabelText("Largest amount"));
    await user.type(screen.getByLabelText("Largest amount"), "abc");
    expect(await screen.findByRole("alert")).toHaveTextContent("plain decimals");
    await user.clear(screen.getByLabelText("Largest amount"));
    expect(await screen.findByRole("status")).toHaveTextContent(/More than 0 transactions match/);
  });

  it("describes where money came from and went", () => {
    const byId = new Map(accounts.map((a) => [a.id, a]));
    expect(describeAccounts(tx as never, byId as never)).toBe("Checking → Food");
  });
});
