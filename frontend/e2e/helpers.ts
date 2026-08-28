import { expect, type Page } from "@playwright/test";

/** A fresh address per test run, so reruns against one database never collide. */
export function uniqueEmail(name: string): string {
  return `${name}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.com`;
}

export const PASSWORD = "correct horse battery staple";

/** Register through the login page and land wherever a new user lands. */
export async function register(page: Page, email: string, displayName: string): Promise<void> {
  await page.goto("/login");
  await page.getByRole("button", { name: "New here? Create an account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Display name").fill(displayName);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
}

export async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();
}

export async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
}

/** From the "create your first book" form: name it, pick a currency, open it. */
export async function createFirstBook(page: Page, name: string, currency: string): Promise<string> {
  await expect(page.getByRole("heading", { name: "Create your first book" })).toBeVisible();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Base currency").selectOption(currency);
  await page.getByRole("button", { name: "Create book" }).click();
  await expect(page.getByRole("heading", { name: "Balance sheet" })).toBeVisible();
  const match = /\/books\/([0-9a-f-]{36})/.exec(page.url());
  if (!match) throw new Error(`not inside a book: ${page.url()}`);
  return match[1]!;
}

/** Owner only: the Accounts tab's "New account" form. */
export async function createAccount(
  page: Page,
  account: { name: string; type: string; parent?: string; description?: string },
): Promise<void> {
  await page.getByRole("link", { name: "Accounts" }).click();
  await page.getByRole("button", { name: "New account" }).click();
  const form = page.getByRole("form", { name: "New account" });
  await form.getByLabel("Name").fill(account.name);
  await form.getByLabel("Type").selectOption(account.type);
  if (account.parent) await form.getByLabel("Parent").selectOption({ label: account.parent });
  if (account.description) await form.getByLabel("Description").fill(account.description);
  await form.getByRole("button", { name: "Create account" }).click();
  await expect(form).toBeHidden();
  await expect(page.getByRole("link", { name: account.name, exact: true })).toBeVisible();
}
