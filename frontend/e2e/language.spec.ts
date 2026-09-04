/** Choosing a language, and finding it again on the next visit. */
import { expect, test } from "@playwright/test";

import { createFirstBook, logout, register, uniqueEmail, PASSWORD } from "./helpers";

test("the language follows the user across logins, and the numbers follow the language", async ({ page }) => {
  const email = uniqueEmail("nl");
  await register(page, email, "Nel");
  await createFirstBook(page, "Huishouden", "EUR");

  await page.getByLabel("Language").selectOption("nl");
  await expect(page.getByRole("link", { name: "Balans" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Balans" })).toBeVisible();
  await expect(page.getByText(/Dit boek heeft nog geen rekeningen/)).toBeVisible();

  // Stored with the account: a fresh browser state still gets Dutch after login.
  await page.getByRole("button", { name: "Afmelden" }).click();
  await page.evaluate(() => window.localStorage.clear());
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Balans" })).toBeVisible();

  // Dutch number formatting on a register.
  await page.getByRole("link", { name: "Instellingen" }).click();
  await page.getByRole("navigation", { name: "Instellingen" }).getByRole("link", { name: "Rekeningen" }).click();
  await page.getByRole("button", { name: "Nieuwe rekening" }).click();
  const form = page.getByRole("form", { name: "Nieuwe rekening" });
  await form.getByLabel("Naam").fill("Kas");
  await form.getByLabel("Type").selectOption("CASH");
  await form.getByRole("button", { name: "Rekening aanmaken" }).click();
  await page.getByRole("button", { name: "Nieuwe rekening" }).click();
  await form.getByLabel("Naam").fill("Eten");
  await form.getByLabel("Type").selectOption("EXPENSE");
  await form.getByRole("button", { name: "Rekening aanmaken" }).click();
  await page.getByRole("link", { name: "Kas", exact: true }).click();
  await page.getByRole("button", { name: "Nieuwe transactie" }).click();
  const tx = page.getByRole("form", { name: "Nieuwe transactie" });
  await tx.getByLabel("Bedrag van regel 1").fill("-1234.56");
  await tx.getByLabel("Rekening van regel 2").selectOption({ label: "Eten" });
  await tx.getByRole("button", { name: "Boeken" }).click();
  await expect(tx).toBeHidden();
  await expect(page.locator("table.register tfoot .money")).toHaveText("-1.234,56 EUR");

  // And back to English, for the next test's sake.
  await page.getByLabel("Taal").selectOption("en");
  await expect(page.getByRole("heading", { name: "Kas" })).toBeVisible();
  await logout(page);
});
