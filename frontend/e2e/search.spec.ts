/**
 * Finding a transaction: by a word, by an amount, by account and date -- and
 * following the result to the transaction itself.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, uniqueEmail } from "./helpers";

test("search by text, amount and account, then open the hit", async ({ page }) => {
  await register(page, uniqueEmail("seeker"), "Seeker");
  await createFirstBook(page, "Seeker's books", "EUR");
  await createAccount(page, { name: "Current", type: "CHECKING" });
  await createAccount(page, { name: "Groceries", type: "EXPENSE" });
  await createAccount(page, { name: "Rent", type: "EXPENSE" });

  await page.getByRole("link", { name: "Current", exact: true }).click();
  for (const [date, amount, memo, category] of [
    ["2026-03-02", "-84.20", "Supermarket", "Groceries"],
    ["2026-03-03", "-800.00", "March rent", "Rent"],
    ["2026-03-09", "-12.30", "Milk and bread", "Groceries"],
  ] as const) {
    await page.getByRole("button", { name: "New transaction" }).click();
    const form = page.getByRole("form", { name: "New transaction" });
    await form.getByLabel("Date").fill(date);
    await form.getByLabel("Memo", { exact: true }).fill(memo);
    await form.getByLabel("Amount of leg 1").fill(amount);
    await form.getByLabel("Account of leg 2").fill(category);
    await form.getByRole("button", { name: "Post" }).click();
    await expect(form).toBeHidden();
  }

  await page.getByRole("link", { name: "Search" }).click();
  await expect(page.getByRole("heading", { name: "Find transactions" })).toBeVisible();

  // A word from a memo.
  await page.getByLabel("Text").fill("bread");
  const results = page.locator(".search-results");
  await expect(results.getByRole("row")).toHaveCount(2); // header + one hit
  await expect(results).toContainText("Milk and bread");
  await expect(results).toContainText("Current → Groceries");
  await expect(page.getByRole("status")).toHaveText("One transaction matches.");

  // An exact amount instead.
  await page.getByLabel("Text").clear();
  await page.getByLabel("Amount", { exact: true }).fill("800");
  await expect(results).toContainText("March rent");
  await expect(results).not.toContainText("Supermarket");

  // A range in one account over a period: two groceries entries, newest first.
  await page.getByLabel("Amount", { exact: true }).clear();
  await page.getByLabel("Smallest amount").fill("10");
  await page.getByLabel("Largest amount").fill("100");
  await page.getByLabel("Account", { exact: true }).selectOption({ label: "Groceries" });
  await page.getByLabel("From").fill("2026-03-01");
  await page.getByLabel("To").fill("2026-03-31");
  await expect(page.getByRole("status")).toHaveText("2 transactions match.");
  const rows = results.locator("tbody tr");
  await expect(rows.first()).toContainText("2026-03-09");
  await expect(rows.last()).toContainText("2026-03-02");

  // Nothing, said plainly -- and a backwards range is refused before asking.
  await page.getByLabel("Smallest amount").fill("5000");
  await expect(page.getByRole("alert")).toContainText("smallest amount is larger");
  await page.getByLabel("Largest amount").clear();
  await expect(page.getByRole("status")).toHaveText("Nothing matches.");

  // And a hit links to the transaction.
  await page.getByLabel("Smallest amount").clear();
  await page.getByLabel("Text").fill("super");
  await results.getByRole("link", { name: "2026-03-02" }).click();
  await expect(page.getByRole("heading", { name: "Supermarket" })).toBeVisible();
  await expect(page.locator("tfoot")).toContainText("Balanced");
});
