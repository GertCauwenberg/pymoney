/**
 * A new household: the first person registers, creates the book, shares it;
 * the second person registers and finds it, read-only.
 */
import { expect, test } from "@playwright/test";

import { createFirstBook, login, logout, register, settings, uniqueEmail } from "./helpers";

test("a new user registers, creates the first book and shares it read-only", async ({ page }) => {
  const alice = uniqueEmail("alice");
  const bob = uniqueEmail("bob");

  // Alice: register, land on "create your first book", create it.
  await register(page, alice, "Alice");
  await expect(page.getByText(/have no books yet/)).toBeVisible();
  await createFirstBook(page, "Smith Family", "GBP");
  await expect(page.locator(".topbar__book")).toHaveText("Smith Family");
  await expect(page.getByText(/This book has no accounts yet/)).toBeVisible();

  // Owner-only settings are there for her.
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("link", { name: "Sharing" })).toBeVisible();

  // Share with Bob as a viewer.
  await page.getByRole("link", { name: "Sharing" }).click();
  await expect(page.getByRole("heading", { name: "Sharing" })).toBeVisible();
  await expect(page.getByText(alice)).toBeVisible();
  await page.getByLabel("Email").fill(bob);
  await page.getByLabel("Role", { exact: true }).selectOption("viewer");
  await page.getByRole("button", { name: "Share" }).click();
  const bobRow = page.getByRole("row").filter({ hasText: bob });
  await expect(bobRow).toBeVisible();
  await expect(bobRow.getByLabel(`Role of ${bob}`)).toHaveValue("viewer");
  // The last owner cannot demote or remove herself.
  const aliceRow = page.getByRole("row").filter({ hasText: alice });
  await expect(aliceRow.getByLabel(`Role of ${alice}`)).toBeDisabled();
  await expect(aliceRow.getByRole("button", { name: "Revoke" })).toBeDisabled();
  await logout(page);

  // Bob: register with the address Alice used, and the book is simply there.
  await register(page, bob, "Bob");
  await expect(page.getByRole("heading", { name: "Balance sheet" })).toBeVisible();
  await expect(page.locator(".topbar__book")).toHaveText("Smith Family");
  // A viewer: no sharing, and the chart of accounts is read-only.
  await settings(page, "Accounts");
  await expect(page.getByRole("link", { name: "Sharing" })).toHaveCount(0);
  await expect(page.getByText(/Only the book's owners can change it/)).toBeVisible();
  await expect(page.getByRole("button", { name: "New account" })).toHaveCount(0);
  await logout(page);

  // Alice again: the session survived nothing (she logged out), so she logs in.
  await login(page, alice);
  await expect(page.locator(".topbar__book")).toHaveText("Smith Family");
});

test("a wrong password is refused with the server's words", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(uniqueEmail("nobody"));
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toContainText("wrong email or password");
});
