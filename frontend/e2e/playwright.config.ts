import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

/**
 * End-to-end: a real browser against the real API and the real web app.
 *
 * Playwright starts both servers. The API runs from the repository's virtualenv
 * on a fresh SQLite file in a temporary directory (so a run never sees another
 * run's users), with registration open, the session cookie allowed over plain
 * HTTP and the quote scheduler off; Vite serves the app and proxies /api to it,
 * exactly as `make web-dev` does. See frontend/README.md, "End-to-end tests".
 */
const root = resolve(__dirname, "../..");
const apiPort = Number(process.env.PYMONEY_E2E_API_PORT ?? 8765);
const webPort = Number(process.env.PYMONEY_E2E_WEB_PORT ?? 5174);
// Playwright evaluates this file in the main process and again in every
// worker; the scratch directory is made once and handed down through the
// environment, so the workers read the same mailbox the API writes to.
const scratch = process.env.PYMONEY_E2E_SCRATCH ?? mkdtempSync(join(tmpdir(), "pymoney-e2e-"));
process.env.PYMONEY_E2E_SCRATCH = scratch;
const database = join(scratch, "e2e.sqlite");
// The API writes its emails here, one JSON line each; the tests read them the
// way a person reads their inbox. Exposed to the specs through the environment.
const mailbox = join(scratch, "mail.jsonl");
process.env.PYMONEY_E2E_MAILBOX = mailbox;
const python = process.env.PYMONEY_E2E_PYTHON ?? join(root, ".venv", "bin", "python");
// A browser of your own (CI installs Playwright's; a sandbox may only have a
// system Chrome): PYMONEY_E2E_BROWSER=/path/to/chrome.
const executablePath = process.env.PYMONEY_E2E_BROWSER;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "report" }]] : "list",
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    ...(executablePath ? { launchOptions: { executablePath, args: ["--no-sandbox"] } } : {}),
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        `${python} -m pymoney.store.migrate --url sqlite+pysqlite:///${database} && ` +
        `${python} -m uvicorn pymoney.api:app --host 127.0.0.1 --port ${apiPort} --log-level warning`,
      url: `http://127.0.0.1:${apiPort}/health`,
      cwd: root,
      reuseExistingServer: false,
      timeout: 60_000,
      // Show the servers' own output in the log. Without this, a server that
      // fails to come up leaves nothing but "Timed out waiting 60000ms".
      stdout: "pipe",
      env: {
        ...process.env,
        PYMONEY_DATABASE_URL: `sqlite+pysqlite:///${database}`,
        PYMONEY_COOKIE_SECURE: "0",
        PYMONEY_ALLOW_REGISTRATION: "1",
        PYMONEY_QUOTE_INTERVAL_MINUTES: "0",
        PYMONEY_MAINTENANCE_INTERVAL_MINUTES: "0",
        // Activation on, as in production: every registration in these tests
        // goes through the emailed link (see helpers.register).
        PYMONEY_REQUIRE_ACTIVATION: "1",
        PYMONEY_MAIL_BACKEND: "file",
        PYMONEY_MAIL_FILE: mailbox,
        PYMONEY_PUBLIC_URL: `http://127.0.0.1:${webPort}`,
      },
    },
    {
      // --host 127.0.0.1, explicitly: Vite's default host is `localhost`, and
      // Node binds only the *first* address that resolves to. On a machine
      // whose /etc/hosts maps localhost to ::1 as well (GitHub's runners do),
      // that is IPv6 loopback only -- and this config polls, browses and mails
      // links to 127.0.0.1, so the run times out waiting for a server that is
      // up. One interface, named once, on both sides.
      command: `npx vite --port ${webPort} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${webPort}/`,
      cwd: resolve(__dirname, "../apps/web"),
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
      env: { ...process.env, PYMONEY_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
