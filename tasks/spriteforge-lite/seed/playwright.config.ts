import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./public-tests",
  timeout: 20_000,
  expect: { timeout: 5_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    serviceWorkers: "block",
    launchOptions: { args: ["--no-sandbox", "--disable-dev-shm-usage"] },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
