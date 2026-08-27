/** Commodities: adding a currency or a security, editing its display fields, deleting. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { CommoditiesPage } from "../src/pages/CommoditiesPage";

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
  commodity("eur", "EUR", { name: "Euro", symbol: "€" }),
  commodity("usd", "USD", { name: "US Dollar", symbol: "$" }),
  commodity("acme", "ACME", { name: "Acme Corp", kind: "STOCK", minor_unit_factor: 10000, display_precision: 4 }),
];
const accounts = [
  { id: "chk", name: "Checking", path: "Assets:Checking", commodity_id: "eur", commodity: "EUR", account_class: "ASSET", type: "CHECKING", parent_id: null, closed: false, placeholder: false },
  { id: "pos", name: "Acme", path: "Assets:Acme", commodity_id: "acme", commodity: "ACME", account_class: "ASSET", type: "STOCK", parent_id: null, closed: false, placeholder: false },
];

type Handler = (req: { body: unknown }) => { status?: number; body?: unknown };

function server(handlers: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api/, "");
    calls.push({ method, path, body });
    const handler = handlers[`${method} ${path}`];
    if (!handler) {
      return new Response(JSON.stringify({ error: { type: "NotFound", message: `no route ${method} ${path}`, status: 404 } }), { status: 404 });
    }
    const { status = 200, body: out } = handler({ body });
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
      <MemoryRouter initialEntries={["/books/b1/commodities"]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="commodities" element={<CommoditiesPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const reads: Record<string, Handler> = {
  "GET /books/b1/commodities": () => ({ body: commodities }),
  "GET /books/b1/accounts": () => ({ body: accounts }),
};

describe("CommoditiesPage", () => {
  it("lists the base currency first and says what blocks a delete", async () => {
    const { fetch } = server(reads);
    mount(fetch);
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1]!).getByText("EUR")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("· base currency")).toBeInTheDocument();
    const eurDelete = screen.getByRole("button", { name: "Delete EUR" });
    expect(eurDelete).toBeDisabled();
    expect(eurDelete).toHaveAttribute("title", "The book's base currency cannot be deleted.");
    const acmeDelete = screen.getByRole("button", { name: "Delete ACME" });
    expect(acmeDelete).toBeDisabled();
    expect(acmeDelete).toHaveAttribute("title", "Used by 1 account(s).");
    expect(screen.getByRole("button", { name: "Delete USD" })).toBeEnabled();
  });

  it("creates a security with four decimals by default", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "POST /books/b1/commodities": ({ body }) => ({ status: 201, body: commodity("new", (body as { code: string }).code) }),
    });
    mount(fetch);
    await user.click(await screen.findByRole("button", { name: "New commodity" }));
    const form = screen.getByRole("form", { name: "New commodity" });
    expect(within(form).getByLabelText("Smallest unit (decimals)")).toHaveValue(2);
    await user.selectOptions(within(form).getByLabelText("Kind"), "STOCK");
    expect(within(form).getByLabelText("Smallest unit (decimals)")).toHaveValue(4);
    await user.type(within(form).getByLabelText("Code"), "msft");
    await user.type(within(form).getByLabelText("Name"), "Microsoft");
    await user.type(within(form).getByLabelText("ISIN"), "US5949181045");
    await user.type(within(form).getByLabelText("Quote source"), "yahoo");
    await user.click(within(form).getByRole("button", { name: "Create commodity" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "New commodity" })).toBeNull());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      code: "MSFT",
      name: "Microsoft",
      kind: "STOCK",
      scale: 4,
      minor_unit_factor: null,
      display_precision: null,
      symbol: null,
      isin: "US5949181045",
      quote_source: "yahoo",
    });
  });

  it("edits only the display fields, and only those that changed", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "PATCH /books/b1/commodities/usd": ({ body }) => ({ body: { ...commodities[1], ...(body as object) } }),
    });
    mount(fetch);
    await user.click(await screen.findByRole("button", { name: "Edit USD" }));
    const form = screen.getByRole("form", { name: "Edit USD" });
    expect(within(form).getByLabelText("Kind")).toBeDisabled();
    expect(within(form).getByLabelText("Smallest unit (decimals)")).toBeDisabled();
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "United States dollar");
    await user.clear(within(form).getByLabelText("Symbol"));
    await user.clear(within(form).getByLabelText("Display decimals"));
    await user.type(within(form).getByLabelText("Display decimals"), "3");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit USD" })).toBeNull());
    const writes = calls.filter((c) => c.method !== "GET");
    expect(writes).toHaveLength(1);
    expect(writes[0]!.method).toBe("PATCH");
    expect(writes[0]!.body).toEqual({ name: "United States dollar", symbol: null, display_precision: 3 });
  });

  it("sends nothing when nothing changed", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server(reads);
    mount(fetch);
    await user.click(await screen.findByRole("button", { name: "Edit USD" }));
    await user.click(within(screen.getByRole("form", { name: "Edit USD" })).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit USD" })).toBeNull());
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("deletes after confirmation and shows the server's refusal", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...reads,
      "DELETE /books/b1/commodities/usd": () => ({
        status: 409,
        body: { error: { type: "ConflictError", message: "USD has 3 price(s); delete them first", status: 409 } },
      }),
    });
    mount(fetch);
    await user.click(await screen.findByRole("button", { name: "Delete USD" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByText(/USD has 3 price/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("shows a viewer the list and nothing else", async () => {
    const { fetch } = server(reads);
    mount(fetch, "viewer");
    expect(await screen.findByText("Acme Corp")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New commodity" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete / })).toBeNull();
  });
});
