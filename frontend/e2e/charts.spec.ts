/**
 * The graphical reports: the same figures as the tables, drawn.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, uniqueEmail } from "./helpers";

test("expenses pie, period table and net worth line show what was posted", async ({ page }) => {
  await register(page, uniqueEmail("viz"), "Viz");
  await createFirstBook(page, "Viz's books", "EUR");
  await createAccount(page, { name: "Current", type: "CHECKING" });
  await createAccount(page, { name: "Rent", type: "EXPENSE" });
  await createAccount(page, { name: "Coffee", type: "EXPENSE" });

  await page.getByRole("link", { name: "Current", exact: true }).click();
  const year = new Date().getFullYear();
  for (const [date, amount, category] of [
    [`${year}-01-03`, "-800.00", "Rent"],
    [`${year}-01-20`, "-40.00", "Coffee"],
    [`${year}-02-03`, "-800.00", "Rent"],
  ] as const) {
    await page.getByRole("button", { name: "New transaction" }).click();
    const form = page.getByRole("form", { name: "New transaction" });
    await form.getByLabel("Date").fill(date);
    await form.getByLabel("Amount of leg 1").fill(amount);
    await form.getByLabel("Account of leg 2").selectOption({ label: category });
    await form.getByRole("button", { name: "Post" }).click();
    await expect(form).toBeHidden();
  }

  await page.getByRole("link", { name: "Charts" }).click();
  // Expenses by category is the landing chart; year to date covers the postings.
  await expect(page.getByRole("heading", { name: "Expenses by category" })).toBeVisible();
  await page.getByRole("button", { name: "Year to date" }).click();
  const legend = page.locator(".pie__legend");
  await expect(legend.getByRole("row").first()).toContainText("Rent");
  await expect(legend.getByRole("row").first()).toContainText("1,600.00");
  await expect(legend.getByRole("row").first()).toContainText("97.6%");
  await expect(legend.locator("tfoot")).toContainText("1,640.00");

  await page.getByRole("link", { name: "Income and expenses by period" }).click();
  await page.getByRole("button", { name: "Year to date" }).click();
  const grid = page.locator(".period-table");
  await expect(grid.getByRole("columnheader", { name: `${year}-01` })).toBeVisible();
  await expect(grid.getByRole("row", { name: /Rent/ })).toContainText("800.00");
  await expect(grid.getByRole("row", { name: /^Net/ })).toContainText("-1,640.00");

  await page.getByRole("link", { name: "Net worth over time" }).click();
  await expect(page.getByRole("img", { name: "Line chart" })).toBeVisible();
  await expect(page.locator(".line__table")).toContainText(`${year}-02`);
});
