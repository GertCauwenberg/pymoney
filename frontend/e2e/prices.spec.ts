/**
 * Reference data: an editor adds a currency, prices it by hand, and sees the
 * price listed as theirs -- the one that wins the day over any fetched quote.
 */
import { expect, test } from "@playwright/test";

import { createFirstBook, register, settings, uniqueEmail } from "./helpers";

test("add a manual currency and a hand-entered price", async ({ page }) => {
  await register(page, uniqueEmail("dan"), "Dan");
  await createFirstBook(page, "Dan's books", "EUR");

  await settings(page, "Commodities");
  await expect(page.getByRole("row").filter({ hasText: "EUR" })).toContainText("base currency");
  await page.getByRole("button", { name: "New commodity" }).click();
  const form = page.getByRole("form", { name: "New commodity" });
  await form.getByLabel("Code").fill("usd");
  await form.getByLabel("Name").fill("US Dollar");
  await form.getByLabel("Symbol").fill("$");
  // The quote source is a choice of the installed providers, or manual.
  const source = form.getByLabel("Quote source");
  await expect(source.locator("option")).toHaveText(["Manual (no fetching)", "ecb", "yahoo"]);
  await source.selectOption("");
  await form.getByRole("button", { name: "Create commodity" }).click();
  await expect(form).toBeHidden();
  const usd = page.getByRole("row").filter({ hasText: "USD" });
  await expect(usd).toContainText("US Dollar");
  await expect(usd).toContainText("manual");

  await page.getByRole("navigation", { name: "Settings" }).getByRole("link", { name: "Prices" }).click();
  await expect(page.getByText(/No commodity has a quote source yet/)).toBeVisible();
  await page.getByRole("button", { name: "Add price" }).click();
  const price = page.getByRole("form", { name: "Add price" });
  await price.getByLabel("Commodity").selectOption({ label: "USD" });
  await price.getByLabel("In").selectOption({ label: "EUR" });
  await price.getByLabel("Rate").fill("10/11");
  await price.getByLabel("Date").fill("2026-03-02");
  await price.getByRole("button", { name: "Add price" }).click();
  await expect(price).toBeHidden();
  const row = page.getByRole("row").filter({ hasText: "2026-03-02" });
  await expect(row).toContainText("1 US Dollar in Euro");
  await expect(row).toContainText("user");
  // The exact ratio survived: the rate cell carries num/den and shows the decimal.
  await expect(row.locator("td[title='10/11']")).toHaveText("0.90909");
});
