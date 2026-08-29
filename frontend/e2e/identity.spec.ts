/**
 * Getting in and getting out: activation, a forgotten password, deleting a
 * book, deleting the account.
 */
import { expect, test } from "@playwright/test";

import { createFirstBook, linkFromMail, logout, register, uniqueEmail, PASSWORD } from "./helpers";

test("a pending account cannot log in until the link is followed", async ({ page }) => {
  const email = uniqueEmail("pending");
  await page.goto("/login");
  await page.getByRole("button", { name: "New here? Create an account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

  // Trying to log in before activating is refused, with the reason.
  await page.getByRole("button", { name: "Back to log in" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toContainText("not been activated");

  // The link works once.
  const link = linkFromMail(email, /Activate/);
  await page.goto(link);
  await expect(page.getByRole("heading", { name: "Create your first book" })).toBeVisible();
  await logout(page);
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText(/expired or already used/);
});

test("a forgotten password is reset through the emailed link, logging the old session out", async ({ page, browser }) => {
  const email = uniqueEmail("forgetful");
  await register(page, email, "Forgetful");
  await createFirstBook(page, "Forgetful's books", "EUR");

  // A second device (its own browser context, its own cookies) holds a session too.
  const device = await browser.newContext();
  const other = await device.newPage();
  await other.goto("/login");
  await other.getByLabel("Email").fill(email);
  await other.getByLabel("Password").fill(PASSWORD);
  await other.getByRole("button", { name: "Log in" }).click();
  await expect(other.getByRole("button", { name: "Log out" })).toBeVisible();

  await logout(page);
  await page.getByRole("link", { name: "Forgotten your password?" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Email me a link" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

  await page.goto(linkFromMail(email, /Reset/));
  await page.getByLabel("New password").fill("an entirely new password");
  await page.getByLabel("Repeat it").fill("an entirely new password");
  await page.getByRole("button", { name: "Set the password and log in" }).click();
  await expect(page.locator(".topbar__book")).toHaveText("Forgetful's books");

  // The old password is dead, the new one works -- and the other tab was logged out.
  await logout(page);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toContainText("wrong email or password");
  await page.getByLabel("Password").fill("an entirely new password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
  await other.reload();
  await expect(other.getByRole("heading", { name: "Log in" })).toBeVisible();
  await device.close();
});

test("an owner deletes the book by typing its name, then leaves", async ({ page }) => {
  const email = uniqueEmail("leaver");
  await register(page, email, "Leaver");
  await createFirstBook(page, "Short-lived", "EUR");

  // While owning a book, the account cannot be deleted -- and the page says so.
  await page.getByRole("link", { name: "PyMoney" }).click();
  const account = page.getByRole("region", { name: "Delete my account" });
  await expect(account).toContainText("You own a book (Short-lived)");
  await expect(account.getByRole("button", { name: "Delete my account…" })).toBeDisabled();

  // Delete the book from its Sharing page: the name must be typed exactly.
  await page.getByRole("link", { name: "Short-lived" }).click();
  await page.getByRole("link", { name: "Sharing" }).click();
  await page.getByRole("button", { name: "Delete this book…" }).click();
  const confirm = page.getByRole("button", { name: "Delete it, irreversibly" });
  await page.getByLabel(/Type the book's name/).fill("short-lived");
  await expect(confirm).toBeDisabled();
  await page.getByLabel(/Type the book's name/).fill("Short-lived");
  await confirm.click();
  await expect(page.getByRole("heading", { name: "Create your first book" })).toBeVisible();

  // Now the account can go.
  await account.getByRole("button", { name: "Delete my account…" }).click();
  await account.getByLabel("Your password, to confirm").fill(PASSWORD);
  await account.getByRole("button", { name: "Delete my account, irreversibly" }).click();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toContainText("wrong email or password");
  // The address is free again.
  await register(page, email, "Leaver again");
  await expect(page.getByRole("heading", { name: "Create your first book" })).toBeVisible();
});
