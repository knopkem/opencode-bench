import { defineConfig } from "@playwright/test";

const appDir = process.env.APP_DIR || "/app";
const appPort = process.env.APP_PORT || "4173";
const serveScript = process.env.SERVE_SCRIPT || "/opt/spritebench/serve.mjs";
const baseURL = process.env.BASE_URL || `http://127.0.0.1:${appPort}`;

export default defineConfig({
  testDir: ".",
  outputDir: process.env.OUTPUT_DIR || "test-results",
  timeout: 20_000,
  expect: { timeout: 5_000 },
  workers: 1,
  retries: 0,
  reporter: [
    ["json", { outputFile: process.env.RESULT_FILE || "playwright.json" }],
    ["list"],
  ],
  use: {
    baseURL,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    serviceWorkers: "block",
    launchOptions: { args: ["--no-sandbox", "--disable-dev-shm-usage"] },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: `node ${serveScript} ${appDir} ${appPort}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
