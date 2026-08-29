/**
 * Coming from KMyMoney: upload the file, get a book you own, with every
 * balance matching what KMyMoney showed.
 */
import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

import { register, uniqueEmail } from "./helpers";

const FIXTURE = resolve(__dirname, "../../tests/fixtures/household.kmy.xml");

test("a new user imports a KMyMoney file instead of creating a book by hand", async ({ page }) => {
  await register(page, uniqueEmail("mover"), "Mover");
  await expect(page.getByRole("heading", { name: "Create your first book" })).toBeVisible();
  await page.getByRole("button", { name: "Import your .kmy file" }).click();
  const form = page.getByRole("form", { name: "Import from KMyMoney" });
  await form.getByLabel("File").setInputFiles(FIXTURE);
  await expect(form.getByLabel("Book name")).toHaveValue("household.kmy");
  await form.getByLabel("Book name").fill("Our old books");
  await form.getByRole("button", { name: "Import" }).click();

  const report = page.getByRole("region", { name: "Import report" });
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(report).toContainText("Our old books");
  await expect(report).toContainText(/15 transactions/);
  await expect(report).toContainText(/scheduled transactions/);
  await report.getByRole("link", { name: "Open Our old books" }).click();

  // Inside the imported book, as its owner: the balance KMyMoney showed.
  await expect(page.locator(".topbar__book")).toHaveText("Our old books");
  await expect(page.getByRole("link", { name: "Sharing" })).toBeVisible();
  await page.getByRole("link", { name: "Accounts" }).click();
  await expect(page.getByRole("link", { name: "Current Account", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Current Account", exact: true }).click();
  await expect(page.locator("tfoot .money")).toHaveText("8,007.73 EUR");
});
