/** Payee management: list, add, edit, merge, delete the unused. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { PayeesPage } from "../src/pages/PayeesPage";

const accounts = [
  { id: "food", name: "Food", path: "Expenses:Food", account_class: "EXPENSE", type: "EXPENSE", commodity_id: "eur", commodity: "EUR", parent_id: null, closed: false, placeholder: false },
];
const payee = (id: string, name: string, extra: object = {}) => ({ id, name, notes: "", address: "", match_keys: [], default_account_id: null, revision: 1, ...extra });
const payees = [payee("p1", "Acme Groceries", { match_keys: ["ACME"], default_account_id: "food" }), payee("p2", "Acme Grocery's"), payee("p3", "Zed Fuel")];

type Handler = (req: { url: URL; body: unknown }) => { status?: number; body?: unknown };
function server(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api/, "");
    calls.push({ method, path, body });
    const key = Object.keys(handlers).find((k) => {
      const [m, p] = k.split(" ") as [string, string];
      return m === method && path === p;
    });
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: `no route ${method} ${path}`, status: 404 } }), { status: 404 });
    const { status = 200, body: out } = handlers[key]!({ url, body });
    return new Response(out === undefined ? null : JSON.stringify(out), { status: out === undefined ? 204 : status, headers: { "content-type": "application/json" } });
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
            <Route path="settings">
              <Route path="payees" element={<PayeesPage />} />
            </Route>
            <Route path="search" element={<p>search page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}
const reads: Record<string, Handler> = {
  "GET /books/b1/payees": () => ({ body: payees }),
  "GET /books/b1/accounts": () => ({ body: accounts }),
};

describe("PayeesPage", () => {
  it("lists payees with their matches and default account, filters, links to search", async () => {
    const user = userEvent.setup();
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/settings/payees");
    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3, alphabetical
    expect(rows[1]).toHaveTextContent("Acme Groceries");
    expect(rows[1]).toHaveTextContent("ACME");
    expect(rows[1]).toHaveTextContent("Expenses:Food");
    expect(screen.getByRole("link", { name: "Zed Fuel" })).toHaveAttribute("href", "/books/b1/search?payee=p3");
    await user.type(screen.getByLabelText("Filter payees"), "zed");
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });

  it("creates and edits a payee", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/payees": ({ body }) => ({ status: 201, body: payee("p9", (body as { name: string }).name) }),
      "PATCH /books/b1/payees/p3": ({ body }) => ({ body: { ...payees[2], ...(body as object) } }),
    });
    mount(fetch, "/books/b1/settings/payees");
    await user.click(await screen.findByRole("button", { name: "New payee" }));
    const form = screen.getByRole("form", { name: "New payee" });
    await user.type(within(form).getByLabelText("Name"), "Bakery");
    await user.type(within(form).getByLabelText("Also matches"), "BAKERY BV, bakkerij");
    await user.selectOptions(within(form).getByLabelText("Default account"), "food");
    await user.click(within(form).getByRole("button", { name: "Create payee" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: "Bakery", notes: "", address: "", match_keys: ["BAKERY BV", "bakkerij"], default_account_id: "food" });

    await user.click(screen.getByRole("button", { name: "Edit Zed Fuel" }));
    const edit = screen.getByRole("form", { name: "Edit Zed Fuel" });
    await user.clear(within(edit).getByLabelText("Notes"));
    await user.type(within(edit).getByLabelText("Notes"), "the garage on the corner");
    await user.click(within(edit).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    // Only what changed travels.
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ notes: "the garage on the corner" });
  });

  it("merges a duplicate behind a confirmation, and shows why a delete was refused", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/payees/p1/merge": () => ({ body: payees[0] }),
      "DELETE /books/b1/payees/p1": () => ({
        status: 409,
        body: { error: { type: "ConflictError", message: "payee 'Acme Groceries' is named by 12 transaction(s); merge it into another payee instead", status: 409 } },
      }),
      "DELETE /books/b1/payees/p3": () => ({}),
    });
    mount(fetch, "/books/b1/settings/payees?edit=p2");
    const form = await screen.findByRole("form", { name: "Edit Acme Grocery's" });
    await user.selectOptions(within(form).getByLabelText("Merge into"), "p1");
    await user.click(within(form).getByRole("button", { name: "Merge…" }));
    expect(calls.some((c) => c.path.endsWith("/merge"))).toBe(false);
    await user.click(within(form).getByRole("button", { name: "Yes, merge" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/books/b1/payees/p1/merge")).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/merge"))!.body).toEqual({ source_ids: ["p2"], keep_match_keys: true });

    // A used payee cannot be deleted; the server says so, in its words.
    await user.click(screen.getByRole("button", { name: "Delete Acme Groceries" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByText(/named by 12 transaction/)).toBeInTheDocument();
    // An unused one goes.
    await user.click(screen.getByRole("button", { name: "Delete Zed Fuel" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path.endsWith("/p3"))).toBe(true));
  });

  it("is read-only for viewers", async () => {
    const { fetch } = server(reads);
    mount(fetch, "/books/b1/settings/payees", "viewer");
    expect(await screen.findByRole("link", { name: "Zed Fuel" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New payee" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
  });
});
