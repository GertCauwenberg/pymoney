/** Writing to the ledger: posting and editing transactions, managing accounts. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import { AccountRegisterPage } from "../src/ledger/AccountRegisterPage";
import { TransactionPage } from "../src/ledger/TransactionPage";
import { AccountsPage } from "../src/pages/AccountsPage";
import type { BookContext } from "../src/pages/BookShell";

const eur = (minor: number) => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: String(minor / 100) });
const usd = (minor: number) => ({ commodity_id: "usd", commodity: "USD", minor, scale: 100, text: String(minor / 100) });

const account = (id: string, path: string, extra: object = {}) => ({
  id,
  name: path.split(":").pop(),
  path,
  type: "CHECKING",
  account_class: "ASSET",
  parent_id: null,
  commodity_id: "eur",
  commodity: "EUR",
  description: "",
  code: "",
  closed: false,
  placeholder: false,
  notes: "",
  institution: "",
  iban: "",
  revision: 1,
  ...extra,
});

const accounts = [
  account("assets", "Assets", { placeholder: true, type: "ASSET" }),
  account("chk", "Assets:Checking", { parent_id: "assets" }),
  account("wallet", "Assets:Dollar Wallet", { parent_id: "assets", commodity_id: "usd", commodity: "USD", type: "CASH" }),
  account("old", "Assets:Old", { parent_id: "assets", closed: true }),
  account("exp", "Expenses", { placeholder: true, type: "EXPENSE", account_class: "EXPENSE" }),
  account("food", "Expenses:Food", { parent_id: "exp", type: "EXPENSE", account_class: "EXPENSE" }),
];
const commodities = [
  { id: "eur", code: "EUR", name: "Euro", kind: "CURRENCY", symbol: "€", minor_unit_factor: 100, display_precision: 2, isin: null, quote_source: null, revision: 1 },
  { id: "usd", code: "USD", name: "US Dollar", kind: "CURRENCY", symbol: "$", minor_unit_factor: 100, display_precision: 2, isin: null, quote_source: null, revision: 1 },
];
const payees = [{ id: "p1", name: "Acme Payroll", notes: "", address: "", match_keys: [], default_account_id: null, revision: 1 }];

const split = (id: string, account_id: string, minor: number, extra: object = {}) => ({
  id,
  account_id,
  quantity: eur(minor),
  value: eur(minor),
  memo: "",
  payee_id: null,
  reconcile_state: "NOT_RECONCILED",
  reconcile_date: null,
  action: "",
  number: "",
  price: null,
  implied_price: null,
  ...extra,
});
const transaction = (extra: object = {}) => ({
  id: "t1",
  date: "2026-02-01",
  currency_id: "eur",
  currency: "EUR",
  memo: "Groceries",
  payee_id: "p1",
  number: "",
  rounding: "HALF_EVEN",
  entered_at: null,
  imported_id: null,
  reconcile_state: "NOT_RECONCILED",
  locked: false,
  revision: 4,
  splits: [split("s1", "chk", -8420, { reconcile_state: "CLEARED", reconcile_date: "2026-02-02" }), split("s2", "food", 8420)],
  ...extra,
});

type Handler = (req: { url: URL; body: unknown; headers: Record<string, string> }) => { status?: number; body?: unknown };

function server(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const headers = Object.fromEntries(Object.entries((init?.headers as Record<string, string>) ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
    const path = url.pathname.replace(/^\/api/, "");
    calls.push({ method, path, body, headers });
    const key = Object.keys(handlers).find((k) => {
      const [m, p] = k.split(" ") as [string, string];
      return m === method && path === p;
    });
    if (!key) {
      return new Response(JSON.stringify({ error: { type: "NotFound", message: `no route ${method} ${path}`, status: 404 } }), { status: 404 });
    }
    const { status = 200, body: out } = handlers[key]!({ url, body, headers });
    return new Response(out === undefined ? null : JSON.stringify(out), {
      status: out === undefined ? 204 : status,
      headers: { "content-type": "application/json", "x-pymoney-revision": "9" },
    });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function mount(fetch: typeof globalThis.fetch, path: string, role = "owner") {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="accounts" element={<AccountsPage />} />
            <Route path="accounts/:accountId" element={<AccountRegisterPage />} />
            <Route path="transactions/:transactionId" element={<TransactionPage />} />
            <Route path="reports/balance-sheet" element={<p>balance sheet</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const reads: Record<string, Handler> = {
  "GET /books/b1/accounts": () => ({ body: accounts }),
  "GET /books/b1/accounts/chk": () => ({ body: accounts[1] }),
  "GET /books/b1/accounts/wallet": () => ({ body: accounts[2] }),
  "GET /books/b1/payees": () => ({ body: payees }),
  "GET /books/b1/commodities": () => ({ body: commodities }),
  "GET /books/b1/accounts/chk/register": () => ({
    body: { items: [], has_more: false, next_cursor: null, totals: { opening: eur(0), closing: eur(0) } },
  }),
  "GET /books/b1/accounts/wallet/register": () => ({
    body: { items: [], has_more: false, next_cursor: null, totals: { opening: usd(0), closing: usd(0) } },
  }),
  "GET /books/b1/transactions/t1": () => ({ body: transaction() }),
};

describe("posting from the register", () => {
  it("posts a transaction with a residual leg and a payee created on the spot", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/payees": ({ body }) => ({ status: 201, body: { ...payees[0], id: "p2", name: (body as { name: string }).name } }),
      "POST /books/b1/transactions": ({ body }) => ({ status: 201, body: transaction({ id: "t9", ...(body as object) }) }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });

    // The register's account is already the first leg.
    expect(within(form).getByLabelText("Account of leg 1")).toHaveValue("chk");
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-84.20");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");
    expect(within(form).getByText("blank leg gets")).toBeInTheDocument();
    expect(within(form).getByText("84.20")).toBeInTheDocument();

    await user.selectOptions(within(form).getByLabelText("Payee"), "__new__");
    await user.type(within(form).getByLabelText("New payee name"), "Lidl");
    await user.type(within(form).getByLabelText("Memo"), "Weekly shop");
    await user.click(within(form).getByRole("button", { name: "Post" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "New transaction" })).toBeNull());
    const payee = calls.find((c) => c.method === "POST" && c.path.endsWith("/payees"))!;
    expect(payee.body).toMatchObject({ name: "Lidl" });
    const post = calls.find((c) => c.method === "POST" && c.path.endsWith("/transactions"))!;
    expect(post.body).toEqual({
      date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      currency_id: "eur",
      memo: "Weekly shop",
      number: "",
      payee_id: "p2",
      rounding: "HALF_EVEN",
      legs: [
        { account_id: "chk", value: "-84.20", quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null },
        { account_id: "food", value: null, quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null },
      ],
    });
    // Amounts travel as strings, never as JSON numbers.
    expect(typeof (post.body as { legs: { value: unknown }[] }).legs[0]!.value).toBe("string");
    expect(post.headers["idempotency-key"]).toMatch(/^web-/);
    // The register is refetched afterwards.
    expect(calls.filter((c) => c.method === "GET" && c.path.endsWith("/register")).length).toBeGreaterThan(1);
  });

  it("asks for a quantity when a leg's account is held in another currency", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/transactions": ({ body }) => ({ status: 201, body: transaction({ id: "t9", ...(body as object) }) }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-100");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "wallet");
    await user.click(within(form).getByRole("button", { name: "Post" }));
    expect(await within(form).findByText("USD amount is needed: this account is not held in EUR.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.type(within(form).getByLabelText("Quantity of leg 2 in USD"), "108.50");
    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect((post.body as { legs: object[] }).legs[1]).toMatchObject({ account_id: "wallet", value: null, quantity: "108.50" });
  });

  it("does not send an incomplete draft, and shows the server's refusal", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/transactions": () => ({
        status: 422,
        body: { error: { type: "UnbalancedTransactionError", message: "legs do not balance: off by 1 EUR", status: 422 } },
      }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.click(within(form).getByRole("button", { name: "Post" }));
    expect(await within(form).findByText("Choose an account.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.type(within(form).getByLabelText("Amount of leg 1"), "-10");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");
    await user.type(within(form).getByLabelText("Amount of leg 2"), "9");
    expect(within(form).getByText("off by")).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Post" }));
    expect(await screen.findByText(/legs do not balance/)).toBeInTheDocument();
    // The form stays, with what was typed.
    expect(within(form).getByLabelText("Amount of leg 1")).toHaveValue("-10");
  });

  it("offers nothing to a viewer", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/accounts/chk", "viewer");
    expect(await screen.findByRole("heading", { name: "Checking" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New transaction" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Edit account" })).toBeNull();
  });

  it("lets an editor post but not edit the account", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/accounts/chk", "editor");
    expect(await screen.findByRole("button", { name: "New transaction" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit account" })).toBeNull();
  });
});

describe("editing a transaction", () => {
  it("prefills the stored figures and sends the whole set of legs back, marks included", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "PATCH /books/b1/transactions/t1": ({ body }) => ({ body: transaction({ ...(body as object), splits: transaction().splits }) }),
    });
    mount(fetch, "/books/b1/transactions/t1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit transaction" });
    expect(within(form).getByLabelText("Date")).toHaveValue("2026-02-01");
    expect(within(form).getByLabelText("Payee")).toHaveValue("p1");
    expect(within(form).getByLabelText("Amount of leg 1")).toHaveValue("-84.20");
    expect(within(form).getByLabelText("Amount of leg 2")).toHaveValue("84.20");
    expect(within(form).getByText("balanced")).toBeInTheDocument();

    const memo = within(form).getByLabelText("Memo");
    await user.clear(memo);
    await user.type(memo, "Groceries and wine");
    const amount2 = within(form).getByLabelText("Amount of leg 2");
    await user.clear(amount2);
    await user.type(amount2, "84.2");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit transaction" })).toBeNull());
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).toEqual({
      date: "2026-02-01",
      memo: "Groceries and wine",
      number: "",
      payee_id: "p1",
      legs: [
        { account_id: "chk", value: "-84.20", quantity: null, memo: "", action: "", number: "", reconcile_state: "CLEARED", reconcile_date: "2026-02-02", payee_id: null },
        { account_id: "food", value: "84.2", quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null },
      ],
    });
  });

  it("deletes after confirmation and returns to the account's register", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "DELETE /books/b1/transactions/t1": () => ({}),
    });
    mount(fetch, "/books/b1/transactions/t1");
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByRole("heading", { name: "Checking" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("cannot touch a reconciled transaction, and says why", async () => {
    const { fetch } = server({ ...reads, "GET /books/b1/transactions/t1": () => ({ body: transaction({ locked: true, reconcile_state: "RECONCILED" }) }) });
    mount(fetch, "/books/b1/transactions/t1");
    const edit = await screen.findByRole("button", { name: "Edit" });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute("title", expect.stringMatching(/reconciled/));
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("shows no write controls to a viewer", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/transactions/t1", "viewer");
    expect(await screen.findByRole("heading", { name: "Acme Payroll" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});

describe("the chart of accounts", () => {
  it("creates an account under a parent of the same class", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/accounts": ({ body }) => ({ status: 201, body: account("new", `Assets:${(body as { name: string }).name}`) }),
    });
    mount(fetch, "/books/b1/accounts");
    await user.click(await screen.findByRole("button", { name: "New account" }));
    const form = screen.getByRole("form", { name: "New account" });
    await user.type(within(form).getByLabelText("Name"), "Holiday: Fund");
    await user.selectOptions(within(form).getByLabelText("Type"), "SAVINGS");
    // Only asset accounts are offered as parents for a savings account.
    const parents = within(within(form).getByLabelText("Parent")).getAllByRole("option").map((o) => o.textContent);
    expect(parents).toEqual(["— top level —", "Assets", "Assets:Checking", "Assets:Dollar Wallet"]);
    await user.selectOptions(within(form).getByLabelText("Parent"), "assets");
    await user.type(within(form).getByLabelText("Description"), "For the summer");
    await user.click(within(form).getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "New account" })).toBeNull());
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({
      name: "Holiday Fund",
      type: "SAVINGS",
      parent_id: "assets",
      commodity_id: "eur",
      description: "For the summer",
      code: "",
      placeholder: false,
      notes: "",
      institution: "",
      iban: "",
    });
  });

  it("warns about a sibling with the same name before the server does", async () => {
    const user = userEvent.setup();
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/accounts");
    await user.click(await screen.findByRole("button", { name: "New account" }));
    const form = screen.getByRole("form", { name: "New account" });
    await user.type(within(form).getByLabelText("Name"), "checking");
    await user.selectOptions(within(form).getByLabelText("Parent"), "assets");
    expect(within(form).getByText("Assets:Checking already exists here.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Create account" })).toBeDisabled();
  });

  it("edits through the endpoint each change belongs to, and only those that changed", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/accounts/chk/rename": () => ({ body: accounts[1] }),
      "POST /books/b1/accounts/chk/move": () => ({ body: accounts[1] }),
      "PATCH /books/b1/accounts/chk": () => ({ body: accounts[1] }),
      "POST /books/b1/accounts/chk/closed": () => ({ body: accounts[1] }),
    });
    // Arriving from the register's "Edit account" link opens the editor.
    mount(fetch, "/books/b1/accounts?edit=chk");
    const form = await screen.findByRole("form", { name: "Edit Checking" });
    expect(within(form).getByLabelText("Type")).toBeDisabled();
    expect(within(form).getByLabelText("Commodity")).toBeDisabled();
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Current account");
    await user.selectOptions(within(form).getByLabelText("Parent"), "");
    await user.type(within(form).getByLabelText("IBAN"), "NL00BANK0123456789");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit Checking" })).toBeNull());
    const writes = calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.path.replace("/books/b1/accounts/chk", ""), c.body]);
    expect(writes).toEqual([
      ["POST", "/rename", { name: "Current account" }],
      ["POST", "/move", { parent_id: null }],
      ["PATCH", "", { iban: "NL00BANK0123456789" }],
    ]);
  });

  it("closes an account from the editor", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({ ...reads, "POST /books/b1/accounts/chk/closed": () => ({ body: { ...accounts[1], closed: true } }) });
    mount(fetch, "/books/b1/accounts");
    await user.click(await screen.findByRole("button", { name: "Edit Checking" }));
    const form = screen.getByRole("form", { name: "Edit Checking" });
    await user.click(within(form).getByLabelText("Closed"));
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/closed"))).toBe(true));
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(1);
    expect(calls.find((c) => c.path.endsWith("/closed"))!.body).toEqual({ closed: true });
  });

  it("deletes only after confirmation, and shows the server's refusal", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "DELETE /books/b1/accounts/food": () => ({
        status: 409,
        body: { error: { type: "ConflictError", message: "'Expenses:Food' is used by 12 transaction(s); merge it into another account instead", status: 409 } },
      }),
      "DELETE /books/b1/accounts/old": () => ({}),
    });
    mount(fetch, "/books/b1/accounts");
    await user.click(await screen.findByRole("button", { name: "Delete Food" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByText(/used by 12 transaction/)).toBeInTheDocument();

    // Closed accounts are hidden until asked for.
    expect(screen.queryByRole("button", { name: "Delete Old" })).toBeNull();
    await user.click(screen.getByLabelText("Show closed"));
    await user.click(screen.getByRole("button", { name: "Delete Old" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(2));
  });

  it("merges one account into another, behind a confirmation", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/accounts/chk/merge": ({ body }) => ({ body: accounts[1] }),
    });
    mount(fetch, "/books/b1/accounts?edit=old");
    const form = await screen.findByRole("form", { name: "Edit Old" });
    // Candidates: same class and commodity, open, no placeholders, not itself:
    // Checking and the Dollar Wallet is out (USD), Assets is out (placeholder).
    const picker = within(form).getByLabelText("Merge into");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "— choose an account —",
      "Assets:Checking",
    ]);
    await user.selectOptions(picker, "chk");
    await user.click(within(form).getByRole("button", { name: "Merge…" }));
    expect(within(form).getByText(/There is no undo/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
    await user.click(within(form).getByRole("button", { name: "Yes, merge" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/books/b1/accounts/chk/merge")).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/merge"))!.body).toEqual({ source_id: "old" });
  });

  it("shows the server's refusal of a merge", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...reads,
      "POST /books/b1/accounts/chk/merge": () => ({
        status: 422,
        body: { error: { type: "ValidationError", message: "cannot merge 'Assets:Old': 2 of its transaction(s) are in a frozen period, which is never rewritten", status: 422 } },
      }),
    });
    mount(fetch, "/books/b1/accounts?edit=old");
    const form = await screen.findByRole("form", { name: "Edit Old" });
    await user.selectOptions(within(form).getByLabelText("Merge into"), "chk");
    await user.click(within(form).getByRole("button", { name: "Merge…" }));
    await user.click(within(form).getByRole("button", { name: "Yes, merge" }));
    expect(await screen.findByText(/frozen period/)).toBeInTheDocument();
  });

  it("is read-only for editors and viewers", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/accounts?edit=chk", "editor");
    expect(await screen.findByRole("link", { name: "Checking" })).toBeInTheDocument();
    expect(screen.getByText(/Only the book's owners can change it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New account" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
  });
});

describe("reconciling", () => {
  const registerRow = (i: number, state: string) => ({
    transaction_id: `t${i}`,
    split_id: `s${i}`,
    date: `2026-02-0${i}`,
    memo: `entry ${i}`,
    payee_id: null,
    account_id: "chk",
    quantity: eur(-1000 * i),
    value: eur(-1000 * i),
    reconcile_state: state,
    number: "",
    running_balance: eur(-1000 * i),
  });
  const registerReads: Record<string, Handler> = {
    ...reads,
    "GET /books/b1/accounts/chk/register": () => ({
      body: {
        items: [registerRow(1, "CLEARED"), registerRow(2, "NOT_RECONCILED"), registerRow(3, "FROZEN")],
        has_more: false,
        next_cursor: null,
        totals: { opening: eur(0), closing: eur(-6000) },
      },
    }),
  };

  it("marks a ticked batch against a statement, cleared rows pre-ticked", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...registerReads,
      "POST /books/b1/accounts/chk/reconcile": () => ({ body: undefined }),
    });
    mount(fetch, "/books/b1/accounts/chk", "editor");
    await user.click(await screen.findByRole("button", { name: "Reconcile" }));

    const bar = screen.getByRole("form", { name: "Reconcile against a statement" });
    // The cleared row starts ticked; the frozen one offers no checkbox at all.
    const boxes = screen.getAllByRole("checkbox", { name: "On the statement" });
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toBeChecked();
    expect(boxes[1]).not.toBeChecked();
    expect(within(bar).getByText(/1 selected/)).toBeInTheDocument();
    // Editors cannot freeze: the option is the server's refusal, so it is not offered.
    expect(within(bar).queryByRole("option", { name: "Frozen" })).toBeNull();

    await user.click(boxes[1]!);
    expect(within(bar).getByText(/2 selected/)).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "Mark them" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/reconcile"))).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.path.endsWith("/reconcile"))!.body as {
      split_ids: string[];
      state: string;
      statement_date: string;
    };
    expect(sent.split_ids.sort()).toEqual(["s1", "s2"]);
    expect(sent.state).toBe("RECONCILED");
    expect(sent.statement_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("owners may choose Frozen, behind a warning", async () => {
    const user = userEvent.setup();
    const { fetch } = server(registerReads);
    mount(fetch, "/books/b1/accounts/chk", "owner");
    await user.click(await screen.findByRole("button", { name: "Reconcile" }));
    const bar = screen.getByRole("form", { name: "Reconcile against a statement" });
    await user.selectOptions(within(bar).getByLabelText("Mark as"), "FROZEN");
    expect(within(bar).getByText(/Frozen is final/)).toBeInTheDocument();
  });

  it("changes one split's state from the transaction page", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/transactions/t1/splits/s1/reconcile": ({ body }) => ({ body: transaction() }),
    });
    mount(fetch, "/books/b1/transactions/t1", "editor");
    const selects = await screen.findAllByRole("combobox", { name: "Reconciliation state" });
    expect(selects).toHaveLength(2);
    // Editors are not offered Frozen here either.
    expect(within(selects[0]!).queryByRole("option", { name: "Frozen" })).toBeNull();
    await user.selectOptions(selects[0]!, "RECONCILED");
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/splits/s1/reconcile"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/splits/s1/reconcile"))!.body as { state: string; on: string | null };
    expect(sent.state).toBe("RECONCILED");
    expect(sent.on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
