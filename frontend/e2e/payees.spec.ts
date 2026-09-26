/**
 * Payees under Settings: one is created on the spot while posting; then it is
 * edited, a duplicate is merged into it, and an unused one is deleted.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, settings, uniqueEmail } from "./helpers";

test("manage payees: edit, merge a duplicate, delete an unused one", async ({ page }) => {
  await register(page, uniqueEmail("payees"), "Pay");
  await createFirstBook(page, "Pay's books", "EUR");
  await createAccount(page, { name: "Current", type: "CHECKING" });
  await createAccount(page, { name: "Groceries", type: "EXPENSE" });

  // Two spellings of the same shop, created on the spot while posting.
  await page.getByRole("link", { name: "Current", exact: true }).click();
  for (const [memo, payee] of [
    ["Weekly shop", "Acme Groceries"],
    ["Milk", "Acme Grocerys"],
  ] as const) {
    await page.getByRole("button", { name: "New transaction" }).click();
    const form = page.getByRole("form", { name: "New transaction" });
    await form.getByLabel("Date").fill("2026-03-02");
    await form.getByLabel("Payee").fill(payee);
    await form.getByLabel("Memo", { exact: true }).fill(memo);
    await form.getByLabel("Amount of leg 1").fill("-10.00");
    await form.getByLabel("Account of leg 2").fill("Groceries");
    await form.getByRole("button", { name: "Post" }).click();
    await expect(form).toBeHidden();
  }

  await settings(page, "Payees");
  await expect(page.getByRole("heading", { name: "Payees" })).toBeVisible();
  const rows = page.locator("table.payees tbody tr");
  await expect(rows).toHaveCount(2);

  // Add one by hand, with a default account.
  await page.getByRole("button", { name: "New payee" }).click();
  const create = page.getByRole("form", { name: "New payee" });
  await create.getByLabel("Name").fill("Corner Bakery");
  await create.getByLabel("Default account").selectOption({ label: "Groceries" });
  await create.getByRole("button", { name: "Create payee" }).click();
  await expect(create).toBeHidden();
  await expect(rows).toHaveCount(3);
  await expect(rows.filter({ hasText: "Corner Bakery" })).toContainText("Groceries");

  // Edit: the duplicate gets its spelling recorded as a match on the original
  // by merging it in.
  await page.getByRole("button", { name: "Edit Acme Grocerys" }).click();
  const edit = page.getByRole("form", { name: "Edit Acme Grocerys" });
  await edit.getByLabel("Merge into").selectOption({ label: "Acme Groceries" });
  await edit.getByRole("button", { name: "Merge…" }).click();
  await edit.getByRole("button", { name: "Yes, merge" }).click();
  await expect(edit).toBeHidden();
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "Acme Groceries" })).toContainText("Acme Grocerys");

  // The merged payee's transactions: both, via the search link.
  await page.getByRole("link", { name: "Acme Groceries" }).click();
  await expect(page.getByRole("status")).toHaveText("2 transactions match.");

  // Back: a used payee cannot be deleted; an unused one can.
  await settings(page, "Payees");
  await page.getByRole("button", { name: "Delete Acme Groceries" }).click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(page.locator(".banner--error")).toContainText(/transaction/);
  await page.getByRole("button", { name: "Delete Corner Bakery" }).click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(rows).toHaveCount(1);
});
