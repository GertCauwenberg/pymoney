/**
 * Reconciling against a statement: tick what the bank saw, mark it in one go,
 * and -- as an owner -- freeze it, after which nothing can touch it again.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, uniqueEmail } from "./helpers";

test("reconcile a statement, then freeze it and find it untouchable", async ({ page }) => {
  await register(page, uniqueEmail("tidy"), "Tidy");
  await createFirstBook(page, "Tidy's books", "EUR");
  await createAccount(page, { name: "Current", type: "CHECKING" });
  await createAccount(page, { name: "Groceries", type: "EXPENSE" });

  await page.getByRole("link", { name: "Current", exact: true }).click();
  for (const [date, amount, memo] of [
    ["2026-03-02", "-12.30", "Milk"],
    ["2026-03-05", "-44.00", "Weekly shop"],
  ] as const) {
    await page.getByRole("button", { name: "New transaction" }).click();
    const form = page.getByRole("form", { name: "New transaction" });
    await form.getByLabel("Date").fill(date);
    await form.getByLabel("Memo", { exact: true }).fill(memo);
    await form.getByLabel("Amount of leg 1").fill(amount);
    await form.getByLabel("Account of leg 2").selectOption({ label: "Groceries" });
    await form.getByRole("button", { name: "Post" }).click();
    await expect(form).toBeHidden();
  }

  // Only the milk is on this statement.
  await page.getByRole("button", { name: "Reconcile" }).click();
  const bar = page.getByRole("form", { name: "Reconcile against a statement" });
  await bar.getByLabel("Statement date").fill("2026-03-31");
  await page
    .locator(".register__row")
    .filter({ hasText: "Milk" })
    .getByRole("checkbox", { name: "On the statement" })
    .check();
  await expect(bar.getByText("1 selected")).toBeVisible();
  await bar.getByRole("button", { name: "Mark them" }).click();
  await expect(bar).toBeHidden();
  const milk = page.locator(".register__row").filter({ hasText: "Milk" });
  await expect(milk.locator(".reconcile--reconciled")).toBeVisible();

  // On the transaction, the owner walks one split up to frozen.
  await milk.getByRole("link", { name: "2026-03-02" }).click();
  const marks = page.getByRole("combobox", { name: "Reconciliation state" });
  await expect(marks).toHaveCount(2);
  await marks.first().selectOption({ label: "Frozen" });
  // Frozen offers no select anymore, and the whole transaction is locked.
  await expect(marks).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Delete", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
});
