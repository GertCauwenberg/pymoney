/** Switching the language: the UI, the numbers, and the account's setting. */
import { ApiClient, translate } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AppI18n } from "../src/i18n";
import { BooksPage } from "../src/pages/BooksPage";
import { LoginPage } from "../src/pages/LoginPage";

const me = { id: "u1", email: "a@example.com", display_name: "A", locale: "en" };

function mount(fetch: typeof globalThis.fetch, path: string) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <AppI18n>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/books" element={<BooksPage />} />
          </Routes>
        </MemoryRouter>
      </AppI18n>
    </ApiProvider>,
  );
}

beforeEach(() => window.localStorage.clear());

it("translates, formats and remembers", async () => {
  const user = userEvent.setup();
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://t").pathname.replace(/^\/api/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    if (path === "/auth/me" && method === "GET") return Response.json(me);
    if (path === "/auth/me" && method === "PATCH") return Response.json({ ...me, locale: body.locale });
    if (path === "/books") return Response.json([
      { id: "b1", name: "Mine", base_currency_id: "eur", revision: 1, role: "owner" },
      { id: "b2", name: "Theirs", base_currency_id: "eur", revision: 1, role: "viewer" },
    ]);
    return new Response("{}", { status: 404 });
  }) as typeof globalThis.fetch;
  mount(fetch, "/books");
  expect(await screen.findByRole("heading", { name: "Your books" })).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Language"), "nl");
  expect(await screen.findByRole("heading", { name: "Uw boeken" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Nieuw boek" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Mijn account verwijderen" })).toBeInTheDocument();
  // Stored with the account, and in this browser.
  await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/auth/me")).toBe(true));
  expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ locale: "nl" });
  expect(window.localStorage.getItem("pymoney.locale")).toBe("nl");
  expect(document.documentElement.lang).toBe("nl");
});

it("follows the account's language once the user is known", async () => {
  const fetch = (async (input: string | URL | Request) => {
    const path = new URL(String(input), "http://t").pathname.replace(/^\/api/, "");
    if (path === "/auth/me") return Response.json({ ...me, locale: "nl" });
    return Response.json([]);
  }) as typeof globalThis.fetch;
  mount(fetch, "/books");
  expect(await screen.findByRole("heading", { name: "Welkom" })).toBeInTheDocument();
});

it("the login page speaks the chosen language before anyone is logged in, and registers with it", async () => {
  const user = userEvent.setup();
  const calls: { path: string; body: unknown }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://t").pathname.replace(/^\/api/, "");
    if (path === "/auth/me") return new Response(JSON.stringify({ detail: "x" }), { status: 401 });
    if (path === "/auth/register") {
      calls.push({ path, body: JSON.parse(String(init!.body)) });
      return Response.json({ status: "pending", email: "n@x.com" }, { status: 202 });
    }
    return new Response("{}", { status: 404 });
  }) as typeof globalThis.fetch;
  mount(fetch, "/login");
  await user.selectOptions(await screen.findByLabelText("Language"), "nl");
  expect(await screen.findByRole("heading", { name: "Aanmelden" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Nieuw hier? Maak een account aan" }));
  await user.type(screen.getByLabelText("E-mail"), "n@x.com");
  await user.type(screen.getByLabelText("Wachtwoord"), "correct horse battery staple");
  await user.click(screen.getByRole("button", { name: "Registreren" }));
  expect(await screen.findByRole("heading", { name: "Controleer uw e-mail" })).toBeInTheDocument();
  expect(calls[0]!.body).toMatchObject({ locale: "nl" });
});

it("translate() substitutes placeholders after the lookup", () => {
  expect(translate("Delete {name}?", "nl", { name: "Visa" })).toBe("Visa verwijderen?");
  expect(translate("Delete {name}?", "en", { name: "Visa" })).toBe("Delete Visa?");
  expect(translate("not a key", "nl")).toBe("not a key");
});
