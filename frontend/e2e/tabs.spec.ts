/**
 * A second browser tab. The session cookie is shared between tabs; the CSRF
 * token lives in sessionStorage, which is not -- so a fresh tab (Ctrl+T, a
 * pasted URL, a restored session) used to read everything and write nothing.
 * GET /auth/me hands the session's token back to its owner, so every tab works.
 */
import { expect, test } from "@playwright/test";

import { createAccount, createFirstBook, register, uniqueEmail } from "./helpers";

test("a fresh tab with the cookie but no sessionStorage can read and write", async ({ page, context }) => {
  await register(page, uniqueEmail("tabs"), "Tabs");
  await createFirstBook(page, "Tab household", "EUR");
  await createAccount(page, { name: "Current", type: "CHECKING" });
  await createAccount(page, { name: "Food", type: "EXPENSE" });
  const url = page.url();

  // A tab opened by hand: same cookies, empty sessionStorage.
  const fresh = await context.newPage();
  await fresh.goto(url);
  await expect(fresh.getByRole("button", { name: "Log out" })).toBeVisible();
  await expect(fresh.getByRole("link", { name: "Current", exact: true })).toBeVisible();

  // And it can write: post a transaction from the new tab.
  await fresh.getByRole("link", { name: "Current", exact: true }).click();
  await fresh.getByRole("button", { name: "New transaction" }).click();
  const form = fresh.getByRole("form", { name: "New transaction" });
  await form.getByLabel("Date").fill("2026-03-01");
  await form.getByLabel("Memo", { exact: true }).fill("From the second tab");
  await form.getByLabel("Amount of leg 1").fill("-5.00");
  await form.getByLabel("Account of leg 2").fill("Food");
  await form.getByRole("button", { name: "Post" }).click();
  await expect(form).toBeHidden();
  await expect(fresh.locator(".register__row")).toContainText("From the second tab");

  // The first tab sees it too (same book, fresh read).
  await page.getByRole("link", { name: "Current", exact: true }).click();
  await expect(page.locator(".register__row")).toContainText("From the second tab");
});
