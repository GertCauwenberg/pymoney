/** The CSV statement import wizard: file, columns, review, report. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { ImportStatementPage } from "../src/ledger/ImportStatementPage";

const me = { id: "u1", email: "alice@example.com", display_name: "Alice" };
const CHECKING = {
  id: "checking",
  name: "Checking",
  path: "Assets:Checking",
  type: "CHECKING",
  account_class: "ASSET",
  commodity: "EUR",
  commodity_id: "eur",
  placeholder: false,
  closed: false,
  parent_id: "assets",
  description: "",
  code: "",
  notes: "",
  institution: "",
  iban: "",
};
const ACCOUNTS = [
  CHECKING,
  { ...CHECKING, id: "groceries", name: "Groceries", path: "Expenses:Groceries", type: "EXPENSE", account_class: "EXPENSE" },
  { ...CHECKING, id: "misc", name: "Uncategorised", path: "Expenses:Uncategorised", type: "EXPENSE", account_class: "EXPENSE" },
];
const PAYEES = [
  { id: "p-tesco", name: "Tesco", notes: "", address: "", match_keys: ["TESCO STORES"], default_account_id: "groceries", revision: 3 },
];

const CSV = "Date,Details,Amount\n2026-02-03,TESCO STORES 7,-42.11\n2026-02-04,A NEW CAFE,-3.80\n";
const SUGGESTED = {
  date: "Date",
  date_format: "",
  amount_kind: "signed" as const,
  amount: "Amount",
  debit: "",
  credit: "",
  decimal: "." as const,
  invert: false,
  description: ["Details"],
  reference: "",
  memo: "",
  skip_rows: 0,
  has_header: true,
  delimiter: ",",
  encoding: "utf-8",
};

function amount(minor: number) {
  return {
    commodity_id: "eur",
    commodity: "EUR",
    minor,
    scale: 100,
    text: (minor / 100).toFixed(2),
  };
}

const PROPOSALS = [
  {
    row_key: "csv:tesco",
    index: 0,
    date: "2026-02-03",
    amount: amount(-4211),
    description: "TESCO STORES 7",
    reference: "",
    memo: "",
    status: "new" as const,
    default_action: "post" as const,
    existing_transaction_id: null,
    existing_account_id: null,
    existing_account_path: "",
    suggested_payee_id: "p-tesco",
    suggested_payee_name: "Tesco",
    suggested_category_id: "groceries",
    suggestion_reason: "payee default",
  },
  {
    row_key: "csv:cafe",
    index: 1,
    date: "2026-02-04",
    amount: amount(-380),
    description: "A NEW CAFE",
    reference: "",
    memo: "",
    status: "new" as const,
    default_action: "post" as const,
    existing_transaction_id: null,
    existing_account_id: null,
    existing_account_path: "",
    suggested_payee_id: null,
    suggested_payee_name: "New Cafe",
    suggested_category_id: null,
    suggestion_reason: "new payee",
  },
];

type Json = Record<string, unknown>;

type Ctx = { form?: FormData | undefined; body?: Json | undefined };
type Handler = (ctx: Ctx) => { status?: number; body?: unknown } | undefined;

function server(overrides: Record<string, Handler> = {}) {
  const calls: {
    method: string;
    path: string;
    form?: Record<string, unknown> | undefined;
    body?: Json | undefined;
  }[] = [];
  const routes: Record<string, Handler> = {
    "GET /auth/me": () => ({ body: me }),
    "GET /books/b1/accounts/checking": () => ({ body: CHECKING }),
    "GET /books/b1/accounts": () => ({ body: ACCOUNTS }),
    "GET /books/b1/payees": () => ({ body: PAYEES }),
    "GET /books/b1/accounts/checking/import-mapping": () => ({ body: null }),
    "POST /books/b1/imports/csv/inspect": () => ({
      body: { columns: ["Date", "Details", "Amount"], sample_rows: [{ Date: "2026-02-03", Details: "TESCO STORES 7", Amount: "-42.11" }], row_count: 2, encoding: "utf-8", delimiter: ",", has_header: true, skip_rows: 0, suggested_mapping: SUGGESTED },
    }),
    "POST /books/b1/accounts/checking/imports/csv/preview": () => ({
      body: { account_id: "checking", mapping: SUGGESTED, proposals: PROPOSALS },
    }),
    "PUT /books/b1/accounts/checking/import-mapping": ({ body }) => ({ body }),
    ...overrides,
  };
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const path = url.pathname.replace(/^\/api/, "");
    const isForm = init?.body instanceof FormData;
    const form = isForm ? (init!.body as FormData) : undefined;
    const body = !isForm && init?.body ? (JSON.parse(String(init.body)) as Json) : undefined;
    calls.push({ method, path, body, form: form ? Object.fromEntries(form.entries()) : undefined });
    const handler = routes[`${method} ${path}`];
    const result = handler?.({ form, body });
    if (!result) return new Response(JSON.stringify({ error: { type: "NotFound", message: `${method} ${path}`, status: 404 } }), { status: 404 });
    const { status = 200, body: out } = result;
    return new Response(out === undefined ? null : JSON.stringify(out), {
      status: out === undefined ? 204 : status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function mount(fetch: typeof globalThis.fetch, role = "editor") {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={["/books/b1/accounts/checking/import"]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="accounts/:accountId" element={<p>the register</p>} />
            <Route path="accounts/:accountId/import" element={<ImportStatementPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

function upload(csv = CSV) {
  return new File([csv], "statement.csv", { type: "text/csv" });
}

describe("CSV statement import", () => {
  it("walks a statement from file to posted transactions", async () => {
    const user = userEvent.setup();
    const committed: Record<string, unknown>[] = [];
    const { fetch, calls } = server({
      "POST /books/b1/accounts/checking/imports/csv/commit": ({ form }) => {
        committed.push({
          mapping: JSON.parse(String(form!.get("mapping"))),
          decisions: JSON.parse(String(form!.get("decisions"))),
          fallback: form!.get("uncategorised_account_id"),
          file: (form!.get("file") as File).name,
        });
        return {
          status: 201,
          body: {
            account_id: "checking",
            account_path: "Assets:Checking",
            rows: 2,
            posted: 2,
            skipped_duplicates: 0,
            skipped_transfers: 0,
            skipped_by_user: 0,
            payees_created: 1,
            uncategorised: 0,
            revision_before: 10,
            revision_after: 12,
            posted_transaction_ids: ["t1", "t2"],
            ok: true,
            problems: [],
            summary: "2 transaction(s) imported into Assets:Checking, 1 payee(s) created.",
          },
        };
      },
    });
    mount(fetch);

    // 1. the file
    await user.upload(await screen.findByLabelText("Statement file"), upload());

    // 2. the columns, pre-filled from the suggestion
    const mapping = await screen.findByRole("button", { name: "Review transactions" });
    expect(screen.getByLabelText("Date")).toHaveValue("Date");
    expect(screen.getByLabelText("Amount column")).toHaveValue("Amount");
    expect(screen.getByText("TESCO STORES 7")).toBeInTheDocument(); // the sample
    await user.click(mapping);

    // 3. the review table: suggestions are pre-filled
    const tesco = (await screen.findByText("TESCO STORES 7")).closest("tr")!;
    expect(within(tesco).getByLabelText(/Payee for/)).toHaveValue("Tesco");
    expect(within(tesco).getByLabelText(/Category for/)).toHaveValue("groceries");
    const cafe = screen.getByText("A NEW CAFE").closest("tr")!;
    expect(within(cafe).getByLabelText(/Payee for/)).toHaveValue("New Cafe");
    expect(within(cafe).getByText("new")).toBeInTheDocument(); // a payee to be created

    // Categorise the cafe and import.
    await user.selectOptions(within(cafe).getByLabelText(/Category for/), "groceries");
    await user.click(screen.getByRole("button", { name: "Import 2 transaction(s)" }));

    // 4. the report
    expect(await screen.findByRole("region", { name: "Import report" })).toBeInTheDocument();
    expect(screen.getByText(/2 transaction\(s\) imported/)).toBeInTheDocument();

    expect(committed).toHaveLength(1);
    expect(committed[0]!.file).toBe("statement.csv");
    expect(committed[0]!.mapping).toMatchObject({ date: "Date", amount: "Amount" });
    expect(committed[0]!.decisions).toEqual([
      { row_key: "csv:tesco", action: "post", payee_id: "p-tesco", new_payee: "", category_id: "groceries" },
      { row_key: "csv:cafe", action: "post", payee_id: null, new_payee: "New Cafe", category_id: "groceries" },
    ]);
    // The mapping is remembered for next week (by the explicit save and the commit).
    expect(calls.some((c) => c.method === "PUT" && c.path.endsWith("/import-mapping"))).toBe(true);
  });

  it("defaults a duplicate to skipped and asks where uncategorised rows go", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      "POST /books/b1/accounts/checking/imports/csv/preview": () => ({
        body: {
          account_id: "checking",
          mapping: SUGGESTED,
          proposals: [
            { ...PROPOSALS[0], row_key: "csv:dup", status: "duplicate", default_action: "skip", existing_transaction_id: "old", suggested_payee_id: null, suggested_payee_name: "", suggested_category_id: null, suggestion_reason: "already imported" },
            { ...PROPOSALS[1], suggested_payee_name: "", suggested_category_id: null },
          ],
        },
      }),
      "POST /books/b1/accounts/checking/imports/csv/commit": ({ form }) => {
        const decisions = JSON.parse(String(form!.get("decisions")));
        expect(decisions[0]).toEqual({ row_key: "csv:dup", action: "skip" });
        expect(form!.get("uncategorised_account_id")).toBe("misc");
        return { status: 201, body: { account_id: "checking", account_path: "Assets:Checking", rows: 2, posted: 1, skipped_duplicates: 1, skipped_transfers: 0, skipped_by_user: 0, payees_created: 0, uncategorised: 1, revision_before: 1, revision_after: 2, posted_transaction_ids: ["t9"], ok: true, problems: [], summary: "1 transaction(s) imported into Assets:Checking; 1 already imported (skipped); 1 posted without a category -- they are in the register waiting for one." } };
      },
    });
    mount(fetch);

    await user.upload(await screen.findByLabelText("Statement file"), upload());
    await user.click(await screen.findByRole("button", { name: "Review transactions" }));

    const dup = (await screen.findByText("already imported")).closest("tr")!;
    expect(within(dup).getByLabelText("Post this row")).not.toBeChecked();

    // One row, no category: the import button is blocked until a home is named.
    expect(screen.getByRole("button", { name: "Import 1 transaction(s)" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Put uncategorised rows in"), "misc");
    await user.click(screen.getByRole("button", { name: "Import 1 transaction(s)" }));

    expect(await screen.findByText(/1 already imported/)).toBeInTheDocument();
  });

  it("stays on the mapping step when the server rejects the columns", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      "POST /books/b1/accounts/checking/imports/csv/preview": () => ({
        status: 422,
        body: { error: { type: "StatementFormatError", message: "'-42.11' is not an amount with ',' for the decimal separator", status: 422 } },
      }),
    });
    mount(fetch);

    await user.upload(await screen.findByLabelText("Statement file"), upload());
    await user.selectOptions(await screen.findByLabelText("Decimal separator"), ",");
    await user.click(screen.getByRole("button", { name: "Review transactions" }));

    expect(await screen.findByText(/is not an amount/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review transactions" })).toBeInTheDocument();
  });

  it("refuses to import with only viewer access", async () => {
    const { fetch } = server();
    mount(fetch, "viewer");
    expect(await screen.findByText(/needs editor access/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Statement file")).not.toBeInTheDocument();
  });
});
