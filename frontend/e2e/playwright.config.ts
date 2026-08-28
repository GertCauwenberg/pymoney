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
const database = join(mkdtempSync(join(tmpdir(), "pymoney-e2e-")), "e2e.sqlite");
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
      env: {
        ...process.env,
        PYMONEY_DATABASE_URL: `sqlite+pysqlite:///${database}`,
        PYMONEY_COOKIE_SECURE: "0",
        PYMONEY_ALLOW_REGISTRATION: "1",
        PYMONEY_QUOTE_INTERVAL_MINUTES: "0",
      },
    },
    {
      command: `npx vite --port ${webPort} --strictPort`,
      url: `http://127.0.0.1:${webPort}/`,
      cwd: resolve(__dirname, "../apps/web"),
      reuseExistingServer: false,
      timeout: 60_000,
      env: { ...process.env, PYMONEY_API_URL: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
