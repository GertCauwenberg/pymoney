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
const tags = [{ id: "g-holiday", name: "Holiday", notes: "", colour: "#ff8800", closed: false, revision: 1 }];

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
  tag_ids: [],
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
  is_stock_split: false,
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
    const raw = init?.body;
    const body =
      raw instanceof FormData
        ? { file: (raw.get("file") as File | null)?.name ?? null }
        : raw
          ? JSON.parse(String(raw))
          : undefined;
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
  "GET /books/b1/tags": () => ({ body: tags }),
  "GET /books/b1/commodities": () => ({ body: commodities }),
  "GET /books/b1/accounts/chk/register": () => ({
    body: { items: [], has_more: false, next_cursor: null, totals: { opening: eur(0), closing: eur(0) } },
  }),
  "GET /books/b1/accounts/wallet/register": () => ({
    body: { items: [], has_more: false, next_cursor: null, totals: { opening: usd(0), closing: usd(0) } },
  }),
  "GET /books/b1/transactions/t1": () => ({ body: transaction() }),
  "GET /books/b1/transactions/t1/attachments": () => ({ body: [] }),
};

const attachment = (extra: object = {}) => ({
  id: "at1",
  transaction_id: "t1",
  filename: "receipt.png",
  content_type: "image/png",
  byte_size: 2048,
  sha256: "0".repeat(64),
  uploaded_by: "owner@example.invalid",
  uploaded_at: "2026-02-02T10:00:00Z",
  ...extra,
});

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
        { account_id: "chk", value: "-84.20", quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null, tag_ids: [] },
        { account_id: "food", value: null, quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null, tag_ids: [] },
      ],
    });
    // Amounts travel as strings, never as JSON numbers.
    expect(typeof (post.body as { legs: { value: unknown }[] }).legs[0]!.value).toBe("string");
    expect(post.headers["idempotency-key"]).toMatch(/^web-/);
    // The register is refetched afterwards.
    expect(calls.filter((c) => c.method === "GET" && c.path.endsWith("/register")).length).toBeGreaterThan(1);
  });

  it("shows an optional quantity field for a leg in another currency", async () => {
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
    await user.type(within(form).getByLabelText("Amount of leg 2"), "100");

    // A quantity may be given, but is not required: left blank, the server
    // values the leg from the book's prices.
    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as { legs: object[] }).legs[1]).toMatchObject({
      account_id: "wallet",
      value: "100",
      quantity: null,
    });
  });

  it("sends a quantity on a foreign leg when one is typed", async () => {
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
    await user.type(within(form).getByLabelText("Amount of leg 2"), "100");
    await user.type(within(form).getByLabelText("Quantity of leg 2 in USD"), "108.50");
    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as { legs: object[] }).legs[1]).toMatchObject({
      account_id: "wallet",
      value: "100",
      quantity: "108.50",
    });
  });

  it("posts in a chosen currency, and fixes it once the transaction exists", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/transactions": ({ body }) => ({ status: 201, body: transaction({ id: "t9", ...(body as object) }) }),
    });
    mount(fetch, "/books/b1/accounts/wallet");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    const currency = within(form).getByLabelText("Currency");
    expect(currency).toHaveValue("eur");
    await user.selectOptions(currency, "usd");
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-10");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");
    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as { currency_id: string }).currency_id).toBe("usd");
  });

  it("does not let the currency be changed while editing", async () => {
    const user = userEvent.setup();
    const { fetch } = server({ ...reads, "PATCH /books/b1/transactions/t1": ({ body }) => ({ body: transaction(body as object) }) });
    mount(fetch, "/books/b1/transactions/t1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit transaction" });
    const currency = within(form).getByLabelText("Currency");
    expect(currency).toBeDisabled();
    expect(currency).toHaveValue("EUR");
  });

  it("records a stock split on a holding, and not on a book-currency account", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/accounts/wallet/stock-split": ({ body }) => ({
        status: 201,
        body: transaction({ id: "s9", is_stock_split: true, splits: [split("x", "wallet", 0)], ...(body as object) }),
      }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    // A EUR account is not a holding: no split control.
    expect(await screen.findByRole("heading", { name: "Checking" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stock split…" })).toBeNull();

    mount(fetch, "/books/b1/accounts/wallet");
    await user.click(await screen.findByRole("button", { name: "Stock split…" }));
    const form = screen.getByRole("form", { name: "Stock split" });
    await user.clear(within(form).getByLabelText("Date"));
    await user.type(within(form).getByLabelText("Date"), "2026-03-01");
    await user.type(within(form).getByLabelText("Ratio"), "3/2");
    await user.click(within(form).getByRole("button", { name: "Record" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Stock split" })).toBeNull());
    const posted = calls.find((c) => c.method === "POST" && c.path.endsWith("/stock-split"))!;
    expect(posted.body).toMatchObject({ date: "2026-03-01", ratio: "3/2" });
  });

  it("refuses a nonsense ratio before it reaches the server", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server(reads);
    mount(fetch, "/books/b1/accounts/wallet");
    await user.click(await screen.findByRole("button", { name: "Stock split…" }));
   const form = screen.getByRole("form", { name: "Stock split" });
    await user.type(within(form).getByLabelText("Ratio"), "1");
    await user.click(within(form).getByRole("button", { name: "Record" }));
    expect(within(form).getByText(/greater than zero and not equal to one/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("attaches a receipt chosen while posting, once the transaction exists", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/transactions": ({ body }) => ({ status: 201, body: transaction({ id: "t9", ...(body as object) }) }),
      "POST /books/b1/transactions/t9/attachments": () => ({ status: 201, body: attachment({ id: "at9", transaction_id: "t9" }) }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-30");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "receipt.png", { type: "image/png" });
    await user.upload(within(form).getByLabelText("Receipts"), file);
    expect(within(form).getByText(/receipt\.png/)).toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "New transaction" })).toBeNull());

    const posted = calls.filter((c) => c.method === "POST").map((c) => c.path);
    // The transaction is posted first; only then, with a real id, the receipt.
    expect(posted).toEqual(["/books/b1/transactions", "/books/b1/transactions/t9/attachments"]);
  });

  it("keeps the form open, with the transaction saved, when a receipt fails to upload", async () => {
    const user = userEvent.setup();
    let attachTries = 0;
    const byKey = new Map<string, object>();
    const { fetch, calls } = server({
      ...reads,
      // Idempotent, like the real endpoint: the same key returns the same row.
      "POST /books/b1/transactions": ({ body, headers }) => {
        const key = headers["idempotency-key"] ?? "";
        if (!byKey.has(key)) byKey.set(key, transaction({ id: "t9", ...(body as object) }));
        return { status: 201, body: byKey.get(key) };
     },
      "POST /books/b1/transactions/t9/attachments": () => {
        attachTries += 1;
        return attachTries === 1
          ? { status: 413, body: { error: { type: "HttpError", message: "the file is larger than 25 MB", status: 413 } } }
          : { status: 201, body: attachment({ id: "at9", transaction_id: "t9" }) };
      },
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-30");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");
    await user.upload(within(form).getByLabelText("Receipts"), new File([new Uint8Array([0x89])], "receipt.png", { type: "image/png" }));
    await user.click(within(form).getByRole("button", { name: "Post" }));

    // The transaction is saved; the form stays so the receipt can be retried.
    expect(await within(form).findByText(/did not upload/)).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "New transaction" })).toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "New transaction" })).toBeNull());
    // Re-posting reuses the idempotency key, so the server keeps one row;
    // the receipt was uploaded on the retry only (the server has one copy).
    const posts = calls.filter((c) => c.method === "POST" && c.path === "/books/b1/transactions");
    expect(new Set(posts.map((c) => c.headers["idempotency-key"])).size).toBe(1);
    expect(byKey.size).toBe(1);
    expect(calls.filter((c) => c.method === "POST" && c.path.endsWith("/attachments"))).toHaveLength(2);
  });

  it("tags a leg with an existing tag and a brand-new one, creating the latter first", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/tags": ({ body }) => ({ status: 201, body: { id: "g-new", name: (body as { name: string }).name, notes: "", colour: null, closed: false, revision: 1 } }),
      "POST /books/b1/transactions": ({ body }) => ({ status: 201, body: transaction({ id: "t9", ...(body as object) }) }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-40");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "food");

    const tagsOfLeg2 = within(form).getByLabelText("Tags of leg 2");
    await user.type(tagsOfLeg2, "Holiday{Enter}");
    await user.type(tagsOfLeg2, "Spain{Enter}");
    expect(within(form).getByText("Holiday")).toBeInTheDocument();
    expect(within(form).getByText("Spain")).toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/transactions"))).toBe(true));
    // The new tag is created before the transaction references it.
    const tagCall = calls.find((c) => c.method === "POST" && c.path.endsWith("/tags"))!;
    expect(tagCall.body).toMatchObject({ name: "Spain" });
    const post = calls.find((c) => c.method === "POST" && c.path.endsWith("/transactions"))!;
    expect((post.body as { legs: { tag_ids: string[] }[] }).legs[0]!.tag_ids).toEqual([]);
    expect((post.body as { legs: { tag_ids: string[] }[] }).legs[1]!.tag_ids).toEqual(["g-holiday", "g-new"]);
  });

  it("keeps a leg's tags on a round trip through the editor", async () => {
    const user = userEvent.setup();
    const tagged = transaction({ splits: [split("s1", "chk", -8420), split("s2", "food", 8420, { tag_ids: ["g-holiday"] })] });
    const { fetch, calls } = server({
      ...reads,
      "GET /books/b1/transactions/t1": () => ({ body: tagged }),
      "PATCH /books/b1/transactions/t1": ({ body }) => ({ body: transaction(body as object) }),
    });
    mount(fetch, "/books/b1/transactions/t1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit transaction" });
    expect(within(form).getByText("Holiday")).toBeInTheDocument();

    const memo = within(form).getByLabelText("Memo");
    await user.type(memo, " and wine");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    const legs = (patch.body as { legs: { account_id: string; tag_ids: string[] }[] }).legs;
    expect(legs.find((l) => l.account_id === "food")!.tag_ids).toEqual(["g-holiday"]);
    expect(legs.find((l) => l.account_id === "chk")!.tag_ids).toEqual([]);
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
        { account_id: "chk", value: "-84.20", quantity: null, memo: "", action: "", number: "", reconcile_state: "CLEARED", reconcile_date: "2026-02-02", payee_id: null, tag_ids: [] },
        { account_id: "food", value: "84.2", quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null, tag_ids: [] },
      ],
    });
  });

  it("lists, uploads and removes attachments", async () => {
    const user = userEvent.setup();
    let stored: ReturnType<typeof attachment>[] = [];
    const { fetch, calls } = server({
      ...reads,
      "GET /books/b1/transactions/t1/attachments": () => ({ body: stored }),
      "POST /books/b1/transactions/t1/attachments": () => {
        stored = [attachment()];
        return { status: 201, body: stored[0] };
      },
      "DELETE /books/b1/attachments/at1": () => {
        stored = [];
        return {};
      },
    });
    mount(fetch, "/books/b1/transactions/t1");

    const section = (await screen.findByRole("heading", { name: "Attachments" })).closest("section")!;
    expect(within(section).getByText(/No receipts or invoices/)).toBeInTheDocument();

    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "receipt.png", { type: "image/png" });
    await user.upload(within(section).getByLabelText("Add an attachment"), file);

    const link = await within(section).findByRole("link", { name: "receipt.png" });
    expect(link).toHaveAttribute("href", "/api/books/b1/attachments/at1");
    expect(within(section).getByText(/2\.0 kB/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/books/b1/transactions/t1/attachments")).toBe(true);

    await user.click(within(section).getByRole("button", { name: "Remove" }));
    await user.click(within(section).getByRole("button", { name: "Yes, remove" }));
    await waitFor(() => expect(within(section).queryByRole("link", { name: "receipt.png" })).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/books/b1/attachments/at1")).toBe(true);
  });

  it("will not open the edit form for a stock split, and says why", async () => {
    const splitTx = transaction({
      is_stock_split: true,
      splits: [split("x", "wallet", 0)],
      memo: "3-for-2 split",
    });
    const { fetch } = server({ ...reads, "GET /books/b1/transactions/t1": () => ({ body: splitTx }) });
    mount(fetch, "/books/b1/transactions/t1");
    const edit = await screen.findByRole("button", { name: "Edit" });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute("title", expect.stringMatching(/recorded, not edited/));
    // ...but it can still be deleted -- that is how you undo one.
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
    expect(screen.getByText(/stock split/)).toBeInTheDocument();
  });

  it("shows attachments read-only to a viewer", async () => {
    const { fetch } = server({
      ...reads,
      "GET /books/b1/transactions/t1/attachments": () => ({ body: [attachment()] }),
    });
    mount(fetch, "/books/b1/transactions/t1", "viewer");
    const section = (await screen.findByRole("heading", { name: "Attachments" })).closest("section")!;
    expect(await within(section).findByRole("link", { name: "receipt.png" })).toBeInTheDocument();
    expect(within(section).queryByLabelText("Add an attachment")).toBeNull();
    expect(within(section).queryByRole("button", { name: "Remove" })).toBeNull();
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

describe("quick entry from the payee", () => {
  it("fills the blank category from the payee's default account and the amount from its last transaction", async () => {
    const user = userEvent.setup();
    const withDefault = [{ ...payees[0], default_account_id: "food" }];
    const { fetch } = server({
      ...reads,
      "GET /books/b1/payees": () => ({ body: withDefault }),
      "GET /books/b1/transactions/search": () => ({ body: { items: [transaction()], has_more: false, limit: 1 } }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.selectOptions(within(form).getByLabelText("Payee"), "p1");
    await waitFor(() => expect(within(form).getByLabelText("Account of leg 2")).toHaveValue("food"));
    // The last transaction with Acme Payroll on this account was -84.20.
    await waitFor(() => expect(within(form).getByLabelText("Amount of leg 1")).toHaveValue("-84.20"));
  });

  it("never overwrites what was typed", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...reads,
      "GET /books/b1/payees": () => ({ body: [{ ...payees[0], default_account_id: "food" }] }),
      "GET /books/b1/transactions/search": () => ({ body: { items: [transaction()], has_more: false, limit: 1 } }),
    });
    mount(fetch, "/books/b1/accounts/chk");
    await user.click(await screen.findByRole("button", { name: "New transaction" }));
    const form = screen.getByRole("form", { name: "New transaction" });
    await user.type(within(form).getByLabelText("Amount of leg 1"), "-5");
    await user.selectOptions(within(form).getByLabelText("Account of leg 2"), "wallet");
    await user.selectOptions(within(form).getByLabelText("Payee"), "p1");
    await new Promise((r) => setTimeout(r, 50));
    expect(within(form).getByLabelText("Amount of leg 1")).toHaveValue("-5");
    expect(within(form).getByLabelText("Account of leg 2")).toHaveValue("wallet");
  });
});

describe("history", () => {
  it("shows who changed a transaction", async () => {
    const { fetch } = server({
      ...reads,
      "GET /books/b1/history": () => ({
        body: [
          { revision: 9, at: "2026-03-01T10:00:00Z", kind: "TRANSACTION", entity_id: "t1", op: "UPDATE", command: "UpdateTransaction", actor: "anna@example.org", note: "" },
          { revision: 4, at: "2026-02-01T10:00:00Z", kind: "TRANSACTION", entity_id: "t1", op: "CREATE", command: "PostTransaction", actor: "bob@example.org", note: "" },
        ],
      }),
    });
    mount(fetch, "/books/b1/transactions/t1");
    expect(await screen.findByText(/last changed by anna@example.org/)).toBeInTheDocument();
    expect(screen.getByText("bob@example.org")).toBeInTheDocument();
    expect(screen.getByText(/transaction created/)).toBeInTheDocument();
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

    // The statement says 30.00 went out (a CHECKING account: debit-normal, shown
    // raw): opening 0 + the ticked -10.00 = -10.00 -> off by 20.00 until the
    // second row is ticked too.
    // Reconciled so far: the pre-ticked -10.00 plus the frozen -30.00 = -40.00.
    // A statement saying -60.00 is off by the unticked -20.00 row.
    await user.type(within(bar).getByLabelText("Statement ending balance"), "-60");
    const difference = () => bar.querySelector(".reconcile-bar__difference")!;
    expect(difference()).toHaveTextContent("differs from the statement by");
    expect(difference()).toHaveTextContent("-20.00");
    await user.click(boxes[1]!);
    expect(within(bar).getByText(/2 selected/)).toBeInTheDocument();
    expect(difference()).toHaveTextContent("matches the statement");
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
