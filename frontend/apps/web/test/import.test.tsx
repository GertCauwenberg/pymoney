/** Importing a KMyMoney file from the books page. */
import { ApiClient } from "@pymoney/core";
import { ApiProvider, createQueryClient } from "@pymoney/core/hooks";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BooksPage } from "../src/pages/BooksPage";

const me = { id: "u1", email: "alice@example.com", display_name: "Alice" };

function mount(fetch: typeof globalThis.fetch) {
  const client = new ApiClient({ baseUrl: "/api", fetch });
  return render(
    <ApiProvider client={client} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={["/books"]}>
        <Routes>
          <Route path="/books" element={<BooksPage />} />
          <Route path="/books/:bookId" element={<p>inside the book</p>} />
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

it("uploads the file as multipart, names the book after it, and shows the report", async () => {
  const user = userEvent.setup();
  const uploads: { name: string | null; fields: Record<string, string>; file: File | null }[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://t").pathname.replace(/^\/api/, "");
    if (path === "/auth/me") return Response.json(me);
    if (path === "/books" && (init?.method ?? "GET") === "GET") return Response.json([]);
    if (path === "/books/import/kmymoney") {
      const form = init!.body as FormData;
      const file = form.get("file") as File;
      uploads.push({ name: file.name, fields: { name: String(form.get("name")), rounding_adjustments: String(form.get("rounding_adjustments")), skip_prices: String(form.get("skip_prices")) }, file });
      expect(init!.headers).not.toHaveProperty("Content-Type"); // the browser sets the boundary
      return Response.json(
        {
          book: { id: "b9", name: form.get("name"), base_currency_id: "eur", revision: 120, role: "owner" },
          ok: true,
          imported: { accounts: 12, transactions: 15, "rounding adjustments": 1 },
          skipped: { "scheduled transactions": 2, budgets: 1 },
          problems: [{ level: "WARNING", category: "account", message: "'A:B' contains ':', renamed to 'A-B'", source_id: "A000007" }],
          adjustments: ["Assets:Dollar Wallet: imported 50.99, KMyMoney shows 51.00, adjusted by 0.01 USD over 5 split(s)"],
          summary: "Imported ...",
        },
        { status: 201 },
      );
    }
    return new Response(JSON.stringify({ error: { type: "NotFound", message: path, status: 404 } }), { status: 404 });
  }) as typeof globalThis.fetch;
  mount(fetch);
  await user.click(await screen.findByRole("button", { name: "Import your .kmy file" }));
  const form = screen.getByRole("form", { name: "Import from KMyMoney" });
  const file = new File(["<KMYMONEY-FILE/>"], "household.kmy", { type: "application/octet-stream" });
  await user.upload(screen.getByLabelText("File"), file);
  expect(screen.getByLabelText("Book name")).toHaveValue("household");
  await user.click(screen.getByLabelText("Import the price history"));
  await user.click(screen.getByRole("button", { name: "Import" }));

  expect(await screen.findByRole("region", { name: "Import report" })).toBeInTheDocument();
  expect(uploads).toHaveLength(1);
  expect(uploads[0]!.fields).toEqual({ name: "household", rounding_adjustments: "true", skip_prices: "true" });
  expect(uploads[0]!.name).toBe("household.kmy");
  expect(screen.getByText(/12 accounts, 15 transactions, 1 rounding adjustments/)).toBeInTheDocument();
  expect(screen.getByText(/2 scheduled transactions, 1 budgets/)).toBeInTheDocument();
  expect(screen.getByText("1 rounding correction(s)")).toBeInTheDocument();
  expect(screen.getByText("1 warning(s)")).toBeInTheDocument();
  expect(form).not.toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "Open household" }));
  expect(await screen.findByText("inside the book")).toBeInTheDocument();
});
