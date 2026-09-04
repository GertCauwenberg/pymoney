/** Creating a book and sharing it: the two things a new user could not do. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { BooksPage } from "../src/pages/BooksPage";
import { SharingPage } from "../src/pages/SharingPage";

const me = { id: "u1", email: "alice@example.com", display_name: "Alice" };

function server(handlers: Record<string, (req: { url: URL; method: string; body: unknown }) => { status?: number; body?: unknown }>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api/, "");
    calls.push({ method, path, body });
    const key = Object.keys(handlers).find((k) => `${method} ${path}` === k);
    if (!key) return new Response(JSON.stringify({ error: { type: "NotFound", message: `no route ${method} ${path}`, status: 404 } }), { status: 404 });
    const { status = 200, body: out } = handlers[key]!({ url, method, body });
    return new Response(out === undefined ? null : JSON.stringify(out), { status: out === undefined ? 204 : status, headers: { "content-type": "application/json" } });
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
          <Route path="/books" element={<BooksPage />} />
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route index element={<Navigate to="reports/balance-sheet" replace />} />
            <Route path="settings">
              <Route path="sharing" element={<SharingPage />} />
            </Route>
            <Route path="reports/balance-sheet" element={<p>report for {context.bookId}</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("BooksPage", () => {
  it("offers to create the first book and opens it once created", async () => {
    const user = userEvent.setup();
    let books: object[] = [];
    const { fetch, calls } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books": () => ({ body: books }),
      "POST /books": ({ body }) => {
        const b = body as { name: string; base_currency_code: string };
        const created = { id: "b1", name: b.name, base_currency_id: "x", revision: 0, role: "owner" };
        books = [created];
        return { status: 201, body: created };
      },
    });
    mount(fetch, "/books");
    expect(await screen.findByText("Create your first book")).toBeInTheDocument();
    expect(screen.getByText(/have no books yet/)).toBeInTheDocument();
    const name = screen.getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Smith Family");
    await user.selectOptions(screen.getByLabelText("Base currency"), "GBP");
    await user.click(screen.getByRole("button", { name: "Create book" }));
    expect(await screen.findByText("report for b1")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({
      name: "Smith Family",
      base_currency_code: "GBP",
      base_currency_name: "Pound Sterling",
      base_currency_symbol: "£",
      base_currency_scale: 2,
    });
  });

  it("lets a currency outside the list be typed in", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books": () => ({ body: [] }),
      "POST /books": () => ({ status: 201, body: { id: "b1", name: "X", base_currency_id: "x", revision: 0, role: "owner" } }),
    });
    mount(fetch, "/books");
    await screen.findByText("Create your first book");
    await user.selectOptions(screen.getByLabelText("Base currency"), "__other__");
    await user.type(screen.getByLabelText("Code"), "inr");
    await user.click(screen.getByRole("button", { name: "Create book" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ base_currency_code: "INR", base_currency_scale: 2 });
  });

  it("shows the server's refusal", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books": () => ({ body: [] }),
      "POST /books": () => ({ status: 409, body: { error: { type: "ConflictError", message: "a book called 'Household' exists", status: 409 } } }),
    });
    mount(fetch, "/books");
    await screen.findByText("Create your first book");
    await user.click(screen.getByRole("button", { name: "Create book" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("a book called 'Household' exists");
  });

  it("with one book redirects, unless the user asked for the list", async () => {
    const one = [{ id: "b1", name: "Only", base_currency_id: "x", revision: 3, role: "owner" }];
    const { fetch } = server({ "GET /auth/me": () => ({ body: me }), "GET /books": () => ({ body: one }) });
    const { unmount } = mount(fetch, "/books");
    // No route matches /books/b1 index here, so "redirected" means the picker is gone.
    await waitFor(() => expect(screen.queryByText("Your books")).toBeNull());
    unmount();

    const client = new ApiClient({ baseUrl: "/api", fetch });
    render(
      <ApiProvider client={client} queryClient={createQueryClient()}>
        <MemoryRouter initialEntries={[{ pathname: "/books", state: { pick: true } }]}>
          <Routes>
            <Route path="/books" element={<BooksPage />} />
          </Routes>
        </MemoryRouter>
      </ApiProvider>,
    );
    expect(await screen.findByText("Your books")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New book" })).toBeInTheDocument();
  });
});

describe("SharingPage", () => {
  const grants = [
    { book_id: "b1", subject: "alice@example.com", role: "owner" },
    { book_id: "b1", subject: "bob@example.com", role: "editor" },
  ];

  it("lists grants, protects the last owner, and shares by email", async () => {
    const user = userEvent.setup();
    let current = [...grants];
    const { fetch, calls } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books/b1/grants": () => ({ body: current }),
      "PUT /books/b1/grants": ({ body }) => {
        const g = body as { subject: string; role: string };
        current = [...current.filter((x) => x.subject !== g.subject), { book_id: "b1", ...g }];
        return { body: { book_id: "b1", ...g } };
      },
    });
    mount(fetch, "/books/b1/settings/sharing");
    expect(await screen.findByText("bob@example.com")).toBeInTheDocument();
    expect(screen.getByText("(you)")).toBeInTheDocument();
    // Alice is the only owner: she cannot demote or remove herself.
    expect(screen.getByLabelText("Role of alice@example.com")).toBeDisabled();
    const revokes = screen.getAllByRole("button", { name: "Revoke" });
    expect(revokes[0]).toBeDisabled();
    expect(revokes[1]).toBeEnabled();

    await user.type(screen.getByLabelText("Email"), "  Carol@Example.com ");
    await user.selectOptions(screen.getByLabelText("Role"), "viewer");
    await user.click(screen.getByRole("button", { name: "Share" }));
    expect(await screen.findByText("carol@example.com")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ subject: "carol@example.com", role: "viewer" });
  });

  it("asks before revoking, encodes the subject, and shows a refusal", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books/b1/grants": () => ({ body: grants }),
      "DELETE /books/b1/grants/bob%40example.com": () => ({
        status: 409,
        body: { error: { type: "HTTPException", message: "this is the last owner; grant another owner first", status: 409 } },
      }),
    });
    mount(fetch, "/books/b1/settings/sharing");
    await screen.findByText("bob@example.com");
    await user.click(screen.getAllByRole("button", { name: "Revoke" })[1]!);
    expect(screen.getByText("Revoke access?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Yes, revoke" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("last owner");
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/books/b1/grants/bob%40example.com")).toBe(true);
  });

  it("is not for editors", async () => {
    const { fetch, calls } = server({ "GET /auth/me": () => ({ body: me }) });
    mount(fetch, "/books/b1/settings/sharing", "editor");
    expect(await screen.findByText("report for b1")).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("/grants"))).toBe(false);
  });
});
