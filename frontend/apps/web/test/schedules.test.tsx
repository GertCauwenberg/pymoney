/** Scheduled transactions: the manage page and the "due" banner. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { DuePanel } from "../src/components/DuePanel";
import { SchedulesPage } from "../src/pages/SchedulesPage";

const ACCOUNTS = [
  { id: "chk", name: "Checking", path: "Assets:Checking", type: "CHECKING", account_class: "ASSET", commodity: "EUR", commodity_id: "eur", placeholder: false, closed: false, parent_id: null, description: "", code: "", notes: "", institution: "", iban: "" },
  { id: "rent", name: "Rent", path: "Expenses:Rent", type: "EXPENSE", account_class: "EXPENSE", commodity: "EUR", commodity_id: "eur", placeholder: false, closed: false, parent_id: null, description: "", code: "", notes: "", institution: "", iban: "" },
];
const SCHEDULE = {
  id: "s1",
  name: "Rent",
  recurrence: { frequency: "MONTHLY", start: "2026-01-01", interval: 1, monthly_by_weekday: false, count: null, until: null },
  recurrence_text: "every month on day 1",
  template: { legs: [{ account_id: "chk", value: "-1200.00", memo: "", is_residual: false }, { account_id: "rent", memo: "", is_residual: true }], memo: "Monthly rent", number: "", rounding: "HALF_EVEN", estimate: false },
  occurrences_done: 0,
  total_occurrences: null,
  next_due: "2026-01-01",
  last_occurrence: null,
  auto_post: false,
  paused: false,
  finished: false,
  upcoming: ["2026-01-01", "2026-02-01", "2026-03-01"],
  revision: 4,
};

function server(overrides: Record<string, (ctx: { body: unknown }) => { status?: number; body?: unknown }> = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const routes: Record<string, (ctx: { body: unknown }) => { status?: number; body?: unknown }> = {
    "GET /auth/me": () => ({ body: { id: "u", email: "a@b.c", display_name: "A" } }),
    "GET /books/b1/accounts": () => ({ body: ACCOUNTS }),
    "GET /books/b1/schedules": () => ({ body: [SCHEDULE] }),
    "GET /books/b1/schedules/due": () => ({
      body: { as_of: "2026-01-01", occurrences: 1, due: [{ schedule: SCHEDULE, pending: ["2026-01-01"], amount: { commodity_id: "eur", commodity: "EUR", minor: 120000, scale: 100, text: "1200.00" } }] },
    }),
    ...overrides,
  };
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://t");
    const method = init?.method ?? "GET";
    const path = url.pathname.replace(/^\/api/, "") + (url.search ? url.search.replace(/\?.*/, "") : "");
    const clean = url.pathname.replace(/^\/api/, "");
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: clean, body });
    const handler = routes[`${method} ${clean}`];
    const res = handler?.({ body });
    if (!res) return new Response(JSON.stringify({ error: { type: "NotFound", message: `${method} ${clean}`, status: 404 } }), { status: 404 });
    const { status = 200, body: out } = res;
    return new Response(out === undefined ? null : JSON.stringify(out), { status: out === undefined ? 204 : status, headers: { "content-type": "application/json" } });
  }) as typeof globalThis.fetch;
  return { fetch, calls };
}

function mount(fetch: typeof globalThis.fetch, element: React.ReactNode, role = "editor") {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  const context: BookContext = { bookId: "b1", baseCurrencyId: "eur", role };
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={["/books/b1/settings/schedules"]}>
        <Routes>
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="settings/schedules" element={element} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("SchedulesPage", () => {
  it("lists schedules and creates one from a two-sided template", async () => {
    const user = userEvent.setup();
    let created: Record<string, unknown> | undefined;
    const { fetch } = server({
      "POST /books/b1/schedules": ({ body }) => {
        created = body as Record<string, unknown>;
        return { status: 201, body: { ...SCHEDULE, id: "s2", name: (body as { name: string }).name } };
      },
    });
    mount(fetch, <SchedulesPage />);

    expect(await screen.findByText("every month on day 1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New schedule" }));
    const form = screen.getByRole("form", { name: "New schedule" });
    await user.type(within(form).getByLabelText("Name"), "Netflix");
    await user.clear(within(form).getByLabelText("Amount"));
    await user.type(within(form).getByLabelText("Amount"), "12.99");
    await user.selectOptions(within(form).getByLabelText("Out of"), "chk");
    await user.selectOptions(within(form).getByLabelText("Category"), "rent");
    await user.selectOptions(within(form).getByLabelText("Unit"), "MONTHLY");
    await user.click(within(form).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(created).toBeDefined());
    expect(created!.name).toBe("Netflix");
    expect((created as { recurrence: { frequency: string } }).recurrence.frequency).toBe("MONTHLY");
    expect((created as { template: { legs: { value?: string }[] } }).template.legs[0]!.value).toBe("-12.99");
  });
});

describe("DuePanel", () => {
  it("shows the count, posts an occurrence, and skips one", async () => {
    const user = userEvent.setup();
    const posted: string[] = [];
    const skipped: string[] = [];
    const { fetch } = server({
      "POST /books/b1/schedules/s1/post": () => {
        posted.push("s1");
        return { status: 201, body: { id: "t1", date: "2026-01-01", currency_id: "eur", currency: "EUR", memo: "Monthly rent", payee_id: null, number: "", rounding: "HALF_EVEN", splits: [], entered_at: null, imported_id: "sched:s1:2026-01-01", reconcile_state: "NOT_RECONCILED", locked: false, is_stock_split: false, revision: 5 } };
      },
      "POST /books/b1/schedules/s1/skip": () => {
        skipped.push("s1");
        return { body: { ...SCHEDULE, occurrences_done: 1, next_due: "2026-02-01" } };
      },
    });
    mount(fetch, <DuePanel bookId="b1" role="editor" />);

    const toggle = await screen.findByRole("button", { name: /1 scheduled transaction/ });
    await user.click(toggle);
    const row = screen.getByText("Rent").closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Post" }));
    await waitFor(() => expect(posted).toEqual(["s1"]));

    await user.click(within(screen.getByText("Rent").closest("li")!).getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(skipped).toEqual(["s1"]));
  });

  it("shows nothing when no occurrence is due", async () => {
    const { fetch } = server({
      "GET /books/b1/schedules/due": () => ({ body: { as_of: "2026-01-01", occurrences: 0, due: [] } }),
    });
    mount(fetch, <DuePanel bookId="b1" role="editor" />);
    await waitFor(() => expect(screen.queryByText(/scheduled transaction/)).not.toBeInTheDocument());
  });
});
