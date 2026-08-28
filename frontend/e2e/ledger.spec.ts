/**
 * Keeping the books: an owner sets up a credit card and a restaurant category,
 * records a dinner on the card, corrects it, checks the balance sheet, and
 * deletes it again.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, uniqueEmail } from "./helpers";

test("enter a credit-card transaction against a restaurant account, edit it, see it in the reports, delete it", async ({
  page,
}) => {
  await register(page, uniqueEmail("carol"), "Carol");
  await createFirstBook(page, "Carol's books", "EUR");

  await createAccount(page, { name: "Visa", type: "CREDIT_CARD", description: "The blue one" });
  await createAccount(page, { name: "Restaurants", type: "EXPENSE" });
  // The chart shows both, under their own trees.
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows.filter({ hasText: "Visa" })).toContainText("credit card");
  await expect(rows.filter({ hasText: "Restaurants" })).toContainText("expense");

  // Into the card's register.
  await page.getByRole("link", { name: "Visa", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Visa" })).toBeVisible();
  await expect(page.getByText("No entries.")).toBeVisible();

  // A dinner: money leaves the card (negative, as stored), the restaurant
  // category takes the rest, and the restaurant is a brand-new payee.
  await page.getByRole("button", { name: "New transaction" }).click();
  const form = page.getByRole("form", { name: "New transaction" });
  await expect(form.getByLabel("Account of leg 1")).toHaveValue(/.+/);
  await form.getByLabel("Date").fill("2026-03-14");
  await form.getByLabel("Payee").selectOption({ label: "New payee…" });
  await form.getByLabel("New payee name").fill("Trattoria da Enzo");
  await form.getByLabel("Memo", { exact: true }).fill("Dinner");
  await form.getByLabel("Amount of leg 1").fill("-62.50");
  await form.getByLabel("Account of leg 2").selectOption({ label: "Restaurants" });
  await expect(form.getByText("blank leg gets")).toBeVisible();
  await expect(form.locator("tfoot")).toContainText("62.50");
  await form.getByRole("button", { name: "Post" }).click();
  await expect(form).toBeHidden();

  // The register shows it with the card's natural sign: a charge is positive.
  const row = page.locator(".register__row").filter({ hasText: "Trattoria da Enzo" });
  await expect(row).toBeVisible();
  await expect(row).toContainText("Dinner");
  await expect(row.locator(".money").first()).toHaveText("62.50");
  await expect(page.locator("tfoot .money")).toHaveText("62.50 EUR");

  // The transaction itself: stored figures, balanced.
  await row.getByRole("link", { name: "2026-03-14" }).click();
  await expect(page.getByRole("heading", { name: "Trattoria da Enzo" })).toBeVisible();
  const splits = page.locator("table.splits tbody tr");
  await expect(splits).toHaveCount(2);
  await expect(splits.filter({ hasText: "Visa" }).locator(".money").last()).toHaveText("-62.50");
  await expect(splits.filter({ hasText: "Restaurants" }).locator(".money").last()).toHaveText("62.50");
  await expect(page.locator("tfoot")).toContainText("Balanced");

  // Correct the memo; the legs go back untouched.
  await page.getByRole("button", { name: "Edit" }).click();
  const edit = page.getByRole("form", { name: "Edit transaction" });
  await expect(edit.getByLabel("Amount of leg 1")).toHaveValue("-62.50");
  await edit.getByLabel("Memo", { exact: true }).fill("Dinner with Dave");
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(edit).toBeHidden();
  await expect(page.getByText("Dinner with Dave", { exact: true })).toBeVisible();

  // The balance sheet sees the debt; the income statement sees the expense.
  await page.getByRole("link", { name: "Balance sheet" }).click();
  const liabilities = page.getByRole("row").filter({ hasText: "Total liabilities" });
  await expect(liabilities.locator(".money")).toHaveText("62.50 EUR");
  await page.getByRole("link", { name: "Income statement" }).click();
  await page.getByLabel("From").fill("2026-03-01");
  await page.getByLabel("To").fill("2026-03-31");
  await expect(page.getByRole("row").filter({ hasText: /^Expenses/ }).locator(".money")).toHaveText("62.50 EUR");

  // Back to the card, into the transaction, and delete it.
  await page.getByRole("link", { name: "Accounts" }).click();
  await page.getByRole("link", { name: "Visa", exact: true }).click();
  await page.locator(".register__row").getByRole("link", { name: "2026-03-14" }).click();
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("button", { name: "Yes, delete" }).click();
  await expect(page.getByRole("heading", { name: "Visa" })).toBeVisible();
  await expect(page.getByText("No entries.")).toBeVisible();
});
