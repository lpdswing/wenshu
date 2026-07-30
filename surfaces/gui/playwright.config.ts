import { defineConfig, devices } from "@playwright/test";

// E2E harness for the GUI. Tests are hermetic: every /v1 request and the event WebSocket are mocked
// at the network layer (see e2e/fixtures.ts), so they run without the Python backend and never
// mutate real state — safe for CI and for asserting regressions in the interaction flows.
const PORT = 5199;
const INTERACTIVE = process.argv.includes("--ui");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // The dev server occasionally leaves a page half-booted during the full hermetic suite.
    // Serve a fresh production build for deterministic test runs; keep HMR for Playwright UI.
    command: INTERACTIVE
      ? `npm run dev -- --port ${PORT} --strictPort`
      : `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
