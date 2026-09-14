/** Onboarding and offboarding: activation, forgotten passwords, deleting a book, leaving. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";

import type { BookContext } from "../src/pages/BookShell";
import { BooksPage } from "../src/pages/BooksPage";
import { LoginPage } from "../src/pages/LoginPage";
import { ActivatePage, ForgotPasswordPage, ResetPasswordPage } from "../src/pages/PasswordPages";
import { SharingPage } from "../src/pages/SharingPage";
import { DeletingPage } from "../src/pages/DeletePage";

const me = { id: "u1", email: "alice@example.com", display_name: "Alice" };
const session = { user: me, csrf_token: "c", token: "t", expires_at: "2027-01-01T00:00:00Z" };

type Handler = (req: { url: URL; body: unknown }) => { status?: number; body?: unknown };

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
    const { status = 200, body: out } = handler({ url, body });
    return new Response(out === undefined ? null : JSON.stringify(out), {
      status: out === undefined ? 204 : status,
      headers: { "content-type": "application/json" },
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
          <Route path="/login" element={<LoginPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/activate" element={<ActivatePage />} />
          <Route path="/books" element={<BooksPage />} />
          <Route path="/books/:bookId" element={<Outlet context={context} />}>
            <Route path="sharing" element={<SharingPage />} />
            <Route path="delete" element={<DeletingPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

const loggedOut: Record<string, Handler> = {
  "GET /auth/me": () => ({ status: 401, body: { detail: "authentication required" } }),
};

describe("registering with activation required", () => {
  it("shows 'check your email' and can resend", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...loggedOut,
      "POST /auth/register": () => ({ status: 202, body: { status: "pending", email: "new@example.com" } }),
      "POST /auth/resend-activation": () => ({ status: 202, body: { status: "pending", email: "new@example.com" } }),
    });
    mount(fetch, "/login");
    await user.click(await screen.findByRole("button", { name: "New here? Create an account" }));
    await user.type(screen.getByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Register" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByText("new@example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send the link again" }));
    expect(await screen.findByText("Sent again.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/resend-activation")!.body).toEqual({ email: "new@example.com" });
    // Back to the *login* form, not the registration one it came from.
    await user.click(screen.getByRole("button", { name: "Back to log in" }));
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
  });

  it("explains a pending account that tries to log in", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...loggedOut,
      "POST /auth/login": () => ({ status: 403, body: { detail: "this account has not been activated yet; follow the link in the email, or ask for a new one" } }),
    });
    mount(fetch, "/login");
    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not been activated yet/);
  });
});

describe("the activation page", () => {
  it("activates with the token from the link and moves on", async () => {
    const { fetch, calls } = server({
      ...loggedOut,
      "POST /auth/activate": () => ({ body: session }),
      "GET /books": () => ({ body: [] }),
    });
    mount(fetch, "/activate?token=abc123xyz");
    expect(await screen.findByText("Welcome")).toBeInTheDocument(); // landed on /books
    expect(calls.find((c) => c.path === "/auth/activate")!.body).toEqual({ token: "abc123xyz" });
  });

  it("says when the link is dead", async () => {
    const { fetch } = server({
      ...loggedOut,
      "POST /auth/activate": () => ({ status: 410, body: { detail: "this activation link is invalid, expired or already used; register again or ask for a new one" } }),
    });
    mount(fetch, "/activate?token=stale");
    expect(await screen.findByRole("alert")).toHaveTextContent(/expired or already used/);
    expect(screen.getByRole("link", { name: "Register again" })).toHaveAttribute("href", "/login");
  });

  it("needs a token", async () => {
    const { fetch } = server(loggedOut);
    mount(fetch, "/activate");
    expect(await screen.findByRole("alert")).toHaveTextContent(/needs the link/);
  });
});

describe("forgotten passwords", () => {
  it("asks for a link and says the same thing whatever the address", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...loggedOut,
      "POST /auth/forgot-password": ({ body }) => ({ status: 202, body: { status: "pending", email: (body as { email: string }).email } }),
    });
    mount(fetch, "/login");
    await user.type(await screen.findByLabelText("Email"), "alice@example.com");
    await user.click(screen.getByRole("link", { name: "Forgotten your password?" }));
    // The address typed on the login page comes along.
    expect(await screen.findByLabelText("Email")).toHaveValue("alice@example.com");
    await user.click(screen.getByRole("button", { name: "Email me a link" }));
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByText(/If/)).toHaveTextContent("alice@example.com");
    expect(calls.find((c) => c.path === "/auth/forgot-password")!.body).toEqual({ email: "alice@example.com" });
  });

  it("resets with the token, insists the two passwords match, and logs in", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({
      ...loggedOut,
      "POST /auth/reset-password": () => ({ body: session }),
      "GET /books": () => ({ body: [] }),
    });
    mount(fetch, "/reset-password?token=tok-1234");
    const first = await screen.findByLabelText("New password");
    await user.type(first, "a brand new password");
    await user.type(screen.getByLabelText("Repeat it"), "a brand new passwor");
    expect(screen.getByText("The two do not match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set the password and log in" })).toBeDisabled();
    await user.type(screen.getByLabelText("Repeat it"), "d");
    await user.click(screen.getByRole("button", { name: "Set the password and log in" }));
    expect(await screen.findByText("Welcome")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/auth/reset-password")!.body).toEqual({ token: "tok-1234", password: "a brand new password" });
  });

  it("offers a new link when the token is dead", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...loggedOut,
      "POST /auth/reset-password": () => ({ status: 410, body: { detail: "this reset link is invalid, expired or already used; ask for a new one" } }),
    });
    mount(fetch, "/reset-password?token=old");
    await user.type(await screen.findByLabelText("New password"), "a brand new password");
    await user.type(screen.getByLabelText("Repeat it"), "a brand new password");
    await user.click(screen.getByRole("button", { name: "Set the password and log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already used/);
    expect(screen.getByRole("link", { name: "Ask for a new link." })).toHaveAttribute("href", "/forgot-password");
  });
});

describe("deleting a book", () => {
  const reads: Record<string, Handler> = {
    "GET /auth/me": () => ({ body: me }),
    "GET /books/b1": () => ({ body: { id: "b1", name: "Smith Family", base_currency_id: "eur", revision: 3, role: "owner" } }),
    "GET /books/b1/grants": () => ({ body: [{ book_id: "b1", subject: me.email, role: "owner" }] }),
    "GET /books": () => ({ body: [] }),
  };

  it("needs the exact name typed back, then goes to the books page", async () => {
    const user = userEvent.setup();
    const { fetch, calls } = server({ ...reads, "DELETE /books/b1": () => ({}) });
    mount(fetch, "/books/b1/delete");
    await user.click(await screen.findByRole("button", { name: "Delete this book…" }));
    const confirm = screen.getByRole("button", { name: "Delete it, irreversibly" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/Type the book's name/), "smith family");
    expect(confirm).toBeDisabled();
    await user.clear(screen.getByLabelText(/Type the book's name/));
    await user.type(screen.getByLabelText(/Type the book's name/), "Smith Family");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(await screen.findByText("Welcome")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")!.body).toEqual({ name: "Smith Family" });
  });

  it("can be backed out of, and shows the server's refusal", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      ...reads,
      "DELETE /books/b1": () => ({ status: 403, body: { detail: "this needs owner on the book; you have editor" } }),
    });
    mount(fetch, "/books/b1/delete");
    await user.click(await screen.findByRole("button", { name: "Delete this book…" }));
    await user.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.getByRole("button", { name: "Delete this book…" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete this book…" }));
    await user.type(screen.getByLabelText(/Type the book's name/), "Smith Family");
    await user.click(screen.getByRole("button", { name: "Delete it, irreversibly" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/needs owner/);
  });
});

describe("deleting the account", () => {
  it("is blocked while the user owns a book, and says which", async () => {
    const { fetch } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books": () => ({
        body: [
          { id: "b1", name: "Mine", base_currency_id: "eur", revision: 1, role: "owner" },
          { id: "b2", name: "Theirs", base_currency_id: "eur", revision: 1, role: "viewer" },
        ],
      }),
    });
    mount(fetch, "/books");
    const section = await screen.findByRole("region", { name: "Delete my account" });
    expect(section).toHaveTextContent(/You own a book \(Mine\)/);
    expect(within(section).getByRole("button", { name: "Delete my account…" })).toBeDisabled();
  });

  it("asks for the password, deletes, and lands on the login page", async () => {
    const user = userEvent.setup();
    let gone = false;
    const { fetch, calls } = server({
      "GET /auth/me": () => (gone ? { status: 401, body: { detail: "authentication required" } } : { body: me }),
      // Two books, or the page would send a single-book user straight into it.
      "GET /books": () => ({
        body: [
          { id: "b2", name: "Theirs", base_currency_id: "eur", revision: 1, role: "viewer" },
          { id: "b3", name: "Also theirs", base_currency_id: "eur", revision: 1, role: "editor" },
        ],
      }),
      "DELETE /auth/me": () => {
        gone = true;
        return {};
      },
    });
    mount(fetch, "/books");
    const section = await screen.findByRole("region", { name: "Delete my account" });
    await user.click(within(section).getByRole("button", { name: "Delete my account…" }));
    await user.type(within(section).getByLabelText("Your password, to confirm"), "correct horse battery staple");
    await user.click(within(section).getByRole("button", { name: "Delete my account, irreversibly" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/auth/me")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.body).toEqual({ password: "correct horse battery staple" });
  });

  it("shows a wrong password as the server says it", async () => {
    const user = userEvent.setup();
    const { fetch } = server({
      "GET /auth/me": () => ({ body: me }),
      "GET /books": () => ({ body: [] }),
      "DELETE /auth/me": () => ({ status: 403, body: { detail: "wrong password" } }),
    });
    mount(fetch, "/books");
    const section = await screen.findByRole("region", { name: "Delete my account" });
    await user.click(within(section).getByRole("button", { name: "Delete my account…" }));
    await user.type(within(section).getByLabelText("Your password, to confirm"), "nope");
    await user.click(within(section).getByRole("button", { name: "Delete my account, irreversibly" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent("wrong password");
  });
});
