import { defineConfig, devices } from "@playwright/test";

// Playwright's automatic failure ARIA snapshot includes password input values.
// Keep credentials out of error-context attachments as well as network traces.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:3001";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  // Authentication payloads must not be captured in traces or network attachments.
  use: { baseURL, trace: "off", extraHTTPHeaders: { "X-DataPilot-CSRF": "1" } },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        channel: process.env.CI ? undefined : "msedge",
      },
    },
  ],
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3001",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
